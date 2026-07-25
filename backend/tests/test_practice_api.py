"""词库与练习接口的 HTTP 测试。

需要数据库在跑且**已导入词库**（pnpm seed）—— 这些接口的行为
依赖真实的话题/词性分布，用假数据测不出干扰项质量。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.core.security import hash_password
from app.models import AnswerEvent, PracticeMode, User, UserProgress, Word, WordList


@pytest_asyncio.fixture
async def auth_client(client, db_session) -> AsyncGenerator[tuple, None]:
    """已登录的客户端。返回 (client, headers, user)。"""
    password = "practice-test-pw"
    user = User(
        username=f"_prac_{uuid.uuid4().hex[:8]}",
        nickname="练习测试",
        password_hash=hash_password(password),
        daily_new_limit=5,
        daily_review_limit=10,
    )
    db_session.add(user)
    await db_session.commit()

    login = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    yield client, headers, user

    # 级联会带走 events / progress
    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


@pytest_asyncio.fixture
async def seeded(db_session) -> WordList:
    """确认词库已导入，否则跳过整个文件的测试。"""
    wl = (
        await db_session.execute(select(WordList).order_by(WordList.created_at).limit(1))
    ).scalar_one_or_none()
    if wl is None:
        pytest.skip("词库未导入，先跑 pnpm seed")

    count = (
        await db_session.execute(
            select(func.count()).select_from(Word).where(Word.word_list_id == wl.id)
        )
    ).scalar_one()
    if count < 100:
        pytest.skip(f"词库只有 {count} 词，不足以测试干扰项质量")
    return wl


# ─────────────────────────────────────────────────────────────
# 鉴权
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("method", "path"), [
    ("get", "/api/word-lists"),
    ("get", "/api/practice/daily?mode=dictation"),
    ("post", "/api/practice/session"),
    ("post", "/api/practice/answer"),
])
async def test_endpoints_require_auth(client, method, path):
    if method == "get":
        r = await client.get(path)
    else:
        r = await client.post(path, json={})
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────
# 词库
# ─────────────────────────────────────────────────────────────

async def test_list_word_lists(auth_client, seeded):
    client, headers, _ = auth_client
    r = await client.get("/api/word-lists", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) >= 1
    assert body[0]["wordCount"] > 0
    assert "isPublic" in body[0]


async def test_word_list_stats(auth_client, seeded):
    client, headers, _ = auth_client
    r = await client.get(f"/api/word-lists/{seeded.id}/stats", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["total"] > 0
    # 新用户：全部是 new
    for mode in ("dictation", "recognition"):
        b = body[mode]
        assert b["new"] == body["total"]
        assert b["learning"] == 0
        assert b["mastered"] == 0
        assert b["new"] + b["learning"] + b["mastered"] == body["total"]


async def test_stats_counts_two_modes_independently(auth_client, seeded, db_session):
    """⭐ 听写与阅读进度独立（ADR-003）—— 统计也必须分开。"""
    client, headers, user = auth_client
    word = (
        await db_session.execute(
            select(Word).where(Word.word_list_id == seeded.id).limit(1)
        )
    ).scalar_one()

    db_session.add(
        UserProgress(
            user_id=user.id,
            word_id=word.id,
            mode=PracticeMode.DICTATION,
            box=5,
            next_review_at=datetime.now(UTC) + timedelta(days=15),
        )
    )
    await db_session.commit()

    body = (await client.get(f"/api/word-lists/{seeded.id}/stats", headers=headers)).json()
    assert body["dictation"]["mastered"] == 1
    assert body["recognition"]["mastered"] == 0, "阅读模式不该受听写进度影响"


async def test_stats_404_for_unknown_list(auth_client):
    client, headers, _ = auth_client
    r = await client.get(f"/api/word-lists/{uuid.uuid4()}/stats", headers=headers)
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────
# 每日任务
# ─────────────────────────────────────────────────────────────

async def test_daily_dictation(auth_client, seeded):
    client, headers, user = auth_client
    r = await client.get("/api/practice/daily?mode=dictation", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["mode"] == "dictation"
    assert body["total"] == len(body["items"])
    # 新用户没有到期词，全是新词，且受 daily_new_limit 约束
    assert body["reviewCount"] == 0
    assert body["newCount"] == user.daily_new_limit
    assert all(i["options"] is None for i in body["items"]), "听写模式不该有选项"
    assert all(i["box"] is None for i in body["items"]), "新词的 box 应为 null"


async def test_daily_recognition_has_options(auth_client, seeded):
    client, headers, _ = auth_client
    body = (
        await client.get("/api/practice/daily?mode=recognition", headers=headers)
    ).json()

    assert body["mode"] == "recognition"
    for item in body["items"]:
        opts = item["options"]
        assert opts is not None and len(opts) == 4
        assert [o["index"] for o in opts] == [1, 2, 3, 4]
        assert len({o["text"] for o in opts}) == 4, "选项有重复"


async def test_options_never_leak_correct_index(auth_client, seeded):
    """⭐ 响应里绝不能出现"哪个是正确答案"的标记。"""
    client, headers, _ = auth_client
    r = await client.get("/api/practice/daily?mode=recognition", headers=headers)
    raw = r.text
    for leak in ("correctIndex", "correct_index", "isCorrect", "correct\":"):
        assert leak not in raw, f"响应泄露了 {leak}"


async def test_options_have_no_pos_prefix(auth_client, seeded):
    """⭐ 选项不带词性前缀 —— 否则用户数前缀就能排除答案。"""
    import re

    client, headers, _ = auth_client
    body = (
        await client.get("/api/practice/daily?mode=recognition", headers=headers)
    ).json()

    offenders = [
        o["text"]
        for item in body["items"]
        for o in item["options"]
        if re.match(r"^[a-z]+\.", o["text"])
    ]
    assert not offenders, f"这些选项带了词性前缀：{offenders[:5]}"


async def test_daily_audio_url_present(auth_client, seeded):
    """seed 阶段音频已 100% 本地化，daily 应该都能拿到 audioUrl。"""
    client, headers, _ = auth_client
    body = (await client.get("/api/practice/daily?mode=dictation", headers=headers)).json()
    assert all(i["audioUrl"] for i in body["items"])


async def test_daily_requires_mode(auth_client):
    client, headers, _ = auth_client
    assert (await client.get("/api/practice/daily", headers=headers)).status_code == 422


async def test_daily_rejects_bad_mode(auth_client):
    client, headers, _ = auth_client
    r = await client.get("/api/practice/daily?mode=nonsense", headers=headers)
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────
# 自由练习
# ─────────────────────────────────────────────────────────────

async def test_free_session_respects_count(auth_client, seeded):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/session",
        headers=headers,
        json={"listId": str(seeded.id), "mode": "dictation", "count": 7, "scope": "new_only"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 7


async def test_free_session_topic_scope(auth_client, seeded):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/session",
        headers=headers,
        json={
            "listId": str(seeded.id),
            "mode": "recognition",
            "count": 5,
            "scope": "topic",
            "topic": "教育",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] > 0


async def test_free_session_topic_without_topic_is_400(auth_client, seeded):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/session",
        headers=headers,
        json={"listId": str(seeded.id), "mode": "dictation", "count": 5, "scope": "topic"},
    )
    assert r.status_code == 400


async def test_free_session_rejects_bad_scope(auth_client, seeded):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/session",
        headers=headers,
        json={"listId": str(seeded.id), "mode": "dictation", "count": 5, "scope": "bogus"},
    )
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────
# 答题
# ─────────────────────────────────────────────────────────────

async def _first_word(db_session, word_list_id) -> Word:
    return (
        await db_session.execute(
            select(Word).where(Word.word_list_id == word_list_id).limit(1)
        )
    ).scalar_one()


async def test_dictation_correct_answer(auth_client, seeded, db_session):
    client, headers, user = auth_client
    word = await _first_word(db_session, seeded.id)

    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": word.word,
            "answeredAt": datetime.now(UTC).isoformat(),
            "deviceId": "test",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["isCorrect"] is True
    assert body["correctAnswer"] == word.word
    assert body["progress"]["box"] == 2, "新词答对应进 Box 2"
    assert body["progress"]["correctCount"] == 1


async def test_dictation_wrong_answer_returns_diff(auth_client, seeded, db_session):
    client, headers, _ = auth_client
    word = await _first_word(db_session, seeded.id)

    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": word.word[:-1] + "zz",
            "answeredAt": datetime.now(UTC).isoformat(),
        },
    )
    body = r.json()
    assert body["isCorrect"] is False
    assert body["diff"], "答错必须返回 diff 用于高亮"
    assert any(d["status"] != "ok" for d in body["diff"])
    assert body["progress"]["box"] == 1, "答错回 Box 1"


async def test_answer_writes_event_and_progress(auth_client, seeded, db_session):
    """⭐ 事件溯源的写路径：追加事件 + 更新进度缓存（ADR-002）。"""
    client, headers, user = auth_client
    word = await _first_word(db_session, seeded.id)

    await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": word.word,
            "answeredAt": datetime.now(UTC).isoformat(),
        },
    )

    events = (
        await db_session.execute(
            select(AnswerEvent).where(
                AnswerEvent.user_id == user.id, AnswerEvent.word_id == word.id
            )
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].is_correct is True

    progress = (
        await db_session.execute(
            select(UserProgress).where(
                UserProgress.user_id == user.id,
                UserProgress.word_id == word.id,
                UserProgress.mode == PracticeMode.DICTATION,
            )
        )
    ).scalar_one()
    assert progress.box == 2


async def test_recognition_answer_by_text(auth_client, seeded, db_session):
    """阅读模式回传选中的**文本**（不是 index）——服务端据此判定。"""
    from app.services.distractor import strip_pos_prefix

    client, headers, _ = auth_client
    word = await _first_word(db_session, seeded.id)

    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "recognition",
            "userInput": strip_pos_prefix(word.meaning_primary),
            "answeredAt": datetime.now(UTC).isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["isCorrect"] is True


async def test_recognition_unknown_is_wrong(auth_client, seeded, db_session):
    """点"不知道"判错，但事件里保留原值以便区分"承认不会"与"蒙错"。"""
    client, headers, user = auth_client
    word = await _first_word(db_session, seeded.id)

    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "recognition",
            "userInput": "unknown",
            "answeredAt": datetime.now(UTC).isoformat(),
        },
    )
    assert r.json()["isCorrect"] is False

    event = (
        await db_session.execute(
            select(AnswerEvent).where(
                AnswerEvent.user_id == user.id,
                AnswerEvent.mode == PracticeMode.RECOGNITION,
            )
        )
    ).scalar_one()
    assert event.user_input == "unknown", "原始输入应保留"


async def test_out_of_order_answer_triggers_full_replay(auth_client, seeded, db_session):
    """⭐ 离线补传的更早事件必须触发全量回放，而非增量。

    增量在乱序时会算错（已有单测固化这个局限），所以服务端必须能识别并回退。
    """
    client, headers, _ = auth_client
    word = await _first_word(db_session, seeded.id)
    now = datetime.now(UTC)

    # 先提交一个"晚"的答错
    late = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": "definitely-wrong",
            "answeredAt": now.isoformat(),
        },
    )
    assert late.json()["wasReplayed"] is False
    assert late.json()["progress"]["box"] == 1

    # 再补传一个更早的答对 —— 应触发全量回放
    early = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": word.word,
            "answeredAt": (now - timedelta(hours=3)).isoformat(),
        },
    )
    body = early.json()
    assert body["wasReplayed"] is True, "更早的事件应触发全量回放"
    # 按真实时间顺序：先对（Box 2）再错（Box 1）
    assert body["progress"]["box"] == 1
    assert body["progress"]["correctCount"] == 1
    assert body["progress"]["wrongCount"] == 1


async def test_answer_404_for_unknown_word(auth_client):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(uuid.uuid4()),
            "mode": "dictation",
            "userInput": "x",
            "answeredAt": datetime.now(UTC).isoformat(),
        },
    )
    assert r.status_code == 404


async def test_answer_rejects_naive_datetime(auth_client, seeded, db_session):
    """answered_at 必须带时区 —— 数据库列是 TIMESTAMPTZ。"""
    client, headers, _ = auth_client
    word = await _first_word(db_session, seeded.id)

    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": "x",
            "answeredAt": "2026-07-25T10:00:00",   # 无时区
        },
    )
    assert r.status_code == 400


async def test_answered_at_is_used_not_server_time(auth_client, seeded, db_session):
    """⭐ 下次复习时刻基于 answered_at，不是服务器当前时间。"""
    client, headers, _ = auth_client
    word = await _first_word(db_session, seeded.id)
    long_ago = datetime(2025, 1, 1, tzinfo=UTC)

    r = await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word.id),
            "mode": "dictation",
            "userInput": word.word,
            "answeredAt": long_ago.isoformat(),
        },
    )
    next_review = datetime.fromisoformat(r.json()["progress"]["nextReviewAt"])
    assert next_review.year == 2025, "复习日期应基于答题时刻而非服务器时间"


async def test_progress_shows_up_in_daily_after_answering(auth_client, seeded, db_session):
    """答过的词不再算新词 —— 端到端验证进度真的生效了。

    注意：要挑**daily 实际返回的**那个词。daily 按词频排序取前 N，
    随便挑一个词很可能不在里面（这里踩过一次）。
    """
    client, headers, _ = auth_client

    before = (await client.get("/api/practice/daily?mode=dictation", headers=headers)).json()
    assert before["items"], "daily 返回空，词库可能没导入"
    target = before["items"][0]

    word = (
        await db_session.execute(select(Word).where(Word.id == uuid.UUID(target["wordId"])))
    ).scalar_one()

    await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": target["wordId"],
            "mode": "dictation",
            "userInput": word.word,
            "answeredAt": datetime.now(UTC).isoformat(),
        },
    )

    after = (await client.get("/api/practice/daily?mode=dictation", headers=headers)).json()
    assert not any(i["wordId"] == target["wordId"] for i in after["items"]), (
        "刚答对的词不该立刻又出现（下次复习在 2 天后）"
    )
