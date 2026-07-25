"""进度接口测试。

⭐ 本文件最重要的一条是 `test_rebuild_restores_deleted_progress`：
   它验证 ADR-002 的核心承诺 —— user_progress 是缓存，删了能从事件完整重建。
   那条测试挂了，说明整个混合式事件溯源的设计已经破了。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.core.security import hash_password
from app.models import AnswerEvent, PracticeMode, User, UserProgress, Word, WordList
from app.services.progress import compute_streak


@pytest_asyncio.fixture
async def auth_client(client, db_session) -> AsyncGenerator[tuple, None]:
    password = "progress-test-pw"
    user = User(
        username=f"_prog_{uuid.uuid4().hex[:8]}",
        nickname="进度测试",
        password_hash=hash_password(password),
    )
    db_session.add(user)
    await db_session.commit()

    login = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    yield client, headers, user

    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


@pytest_asyncio.fixture
async def words(db_session) -> list[Word]:
    rows = list(
        (await db_session.execute(select(Word).limit(5))).scalars().all()
    )
    if len(rows) < 5:
        pytest.skip("词库未导入，先跑 pnpm seed")
    return rows


async def _answer(client, headers, word_id, mode, user_input, at):
    return await client.post(
        "/api/practice/answer",
        headers=headers,
        json={
            "wordId": str(word_id),
            "mode": mode,
            "userInput": user_input,
            "answeredAt": at.isoformat(),
        },
    )


# ─────────────────────────────────────────────────────────────
# 连续天数（纯函数）
# ─────────────────────────────────────────────────────────────

TODAY = date(2026, 7, 25)


def d(day: int) -> date:
    return date(2026, 7, day)


@pytest.mark.parametrize(("days", "expected"), [
    ([], 0),
    ([d(25)], 1),
    ([d(25), d(24), d(23)], 3),
    ([d(24), d(23)], 2),               # 今天还没学，不算断
    ([d(23)], 0),                      # 昨天就断了
    ([d(25), d(23)], 1),               # 中间缺一天
    ([d(25), d(24), d(22), d(21)], 2), # 断在 23
    ([d(20)], 0),                      # 很久没学
])
def test_compute_streak(days, expected):
    assert compute_streak(days, TODAY) == expected


def test_streak_handles_duplicate_days():
    """同一天答多次只算一天。"""
    assert compute_streak([d(25), d(25), d(24), d(24)], TODAY) == 2


def test_streak_ignores_order():
    """输入顺序不该影响结果。"""
    assert compute_streak([d(23), d(25), d(24)], TODAY) == 3


# ─────────────────────────────────────────────────────────────
# 鉴权
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/progress/summary",
    "/api/progress/wrong-words",
])
async def test_get_endpoints_require_auth(client, path):
    assert (await client.get(path)).status_code == 401


async def test_rebuild_requires_auth(client):
    assert (await client.post("/api/progress/rebuild", json={})).status_code == 401


# ─────────────────────────────────────────────────────────────
# 总览
# ─────────────────────────────────────────────────────────────

async def test_summary_empty_user(auth_client):
    client, headers, _ = auth_client
    body = (await client.get("/api/progress/summary", headers=headers)).json()

    assert body["streakDays"] == 0
    assert body["today"]["answered"] == 0
    assert body["today"]["accuracy"] == 0.0
    assert body["totalAnswered"] == 0
    # 盒子分布应有全部 5 个键，值为 0
    assert body["boxes"]["dictation"] == {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
    assert body["dueNow"]["dictation"] == 0


async def test_summary_after_answering(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)

    await _answer(client, headers, words[0].id, "dictation", words[0].word, now)
    await _answer(client, headers, words[1].id, "dictation", "definitely-wrong", now)

    body = (await client.get("/api/progress/summary", headers=headers)).json()
    assert body["today"]["answered"] == 2
    assert body["today"]["correct"] == 1
    assert body["today"]["accuracy"] == 0.5
    assert body["totalAnswered"] == 2
    assert body["streakDays"] == 1
    assert body["boxes"]["dictation"]["1"] == 1   # 答错的
    assert body["boxes"]["dictation"]["2"] == 1   # 答对的


async def test_summary_counts_modes_separately(auth_client, words):
    """听写与阅读进度独立（ADR-003）。"""
    from app.services.distractor import strip_pos_prefix

    client, headers, _ = auth_client
    now = datetime.now(UTC)
    w = words[0]

    await _answer(client, headers, w.id, "dictation", w.word, now)
    await _answer(
        client, headers, w.id, "recognition", strip_pos_prefix(w.meaning_primary), now
    )

    body = (await client.get("/api/progress/summary", headers=headers)).json()
    assert body["boxes"]["dictation"]["2"] == 1
    assert body["boxes"]["recognition"]["2"] == 1
    assert body["totalAnswered"] == 2


async def test_summary_due_now(auth_client, words, db_session, monkeypatch):
    """到期待复习数。"""
    client, headers, user = auth_client
    db_session.add(
        UserProgress(
            user_id=user.id,
            word_id=words[0].id,
            mode=PracticeMode.DICTATION,
            box=1,
            next_review_at=datetime.now(UTC) - timedelta(hours=1),   # 已到期
        )
    )
    db_session.add(
        UserProgress(
            user_id=user.id,
            word_id=words[1].id,
            mode=PracticeMode.DICTATION,
            box=3,
            next_review_at=datetime.now(UTC) + timedelta(days=3),    # 未到期
        )
    )
    await db_session.commit()

    body = (await client.get("/api/progress/summary", headers=headers)).json()
    assert body["dueNow"]["dictation"] == 1


# ─────────────────────────────────────────────────────────────
# ⭐ 时区
# ─────────────────────────────────────────────────────────────

async def test_today_respects_timezone_offset(auth_client, words):
    """⭐ "今日"按客户端本地日期切分，不是 UTC。

    构造一个 UTC 深夜的答题：对东八区用户来说那已经是**第二天早上**。
    不传 offset 按 UTC 算会把它记到前一天。
    """
    client, headers, _ = auth_client

    # 取"东八区的今天凌晨 1 点"，对应 UTC 是前一天 17:00
    now_utc = datetime.now(UTC)
    beijing_now = now_utc + timedelta(minutes=480)
    beijing_early = beijing_now.replace(hour=1, minute=0, second=0, microsecond=0)
    utc_equivalent = beijing_early - timedelta(minutes=480)

    await _answer(client, headers, words[0].id, "dictation", words[0].word, utc_equivalent)

    # 按东八区算：算今天
    tz8 = (
        await client.get(
            "/api/progress/summary", headers=headers, params={"tzOffsetMinutes": 480}
        )
    ).json()
    # 按 UTC 算：可能算昨天（取决于当前钟点）
    utc = (await client.get("/api/progress/summary", headers=headers)).json()

    assert tz8["today"]["answered"] == 1, "东八区视角下这次答题应算今天"
    # 两个视角至少有一个不同才说明 offset 真的生效了；
    # 若恰好同一天则跳过这条断言（UTC 与东八区当天重合的时段）
    if beijing_early.date() != utc_equivalent.date():
        assert utc["today"]["answered"] == 0, "UTC 视角下应算前一天"


@pytest.mark.parametrize("bad_offset", [-721, 841, 9999])
async def test_summary_rejects_invalid_tz_offset(auth_client, bad_offset):
    client, headers, _ = auth_client
    r = await client.get(
        "/api/progress/summary", headers=headers, params={"tzOffsetMinutes": bad_offset}
    )
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────
# 错题本
# ─────────────────────────────────────────────────────────────

async def test_wrong_words_empty(auth_client):
    client, headers, _ = auth_client
    body = (await client.get("/api/progress/wrong-words", headers=headers)).json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_wrong_words_lists_mistakes(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)

    await _answer(client, headers, words[0].id, "dictation", "wrong-1", now)
    await _answer(client, headers, words[0].id, "dictation", "wrong-2", now + timedelta(minutes=1))
    await _answer(client, headers, words[1].id, "dictation", "wrong-x", now)
    await _answer(client, headers, words[2].id, "dictation", words[2].word, now)   # 答对的

    body = (await client.get("/api/progress/wrong-words", headers=headers)).json()
    assert body["total"] == 2, "答对的词不该进错题本"

    top = body["items"][0]
    assert top["wordId"] == str(words[0].id)
    assert top["wrongCount"] == 2, "应按错误次数降序"


async def test_wrong_words_records_recent_inputs(auth_client, words):
    """⭐ 事件溯源"免费送"的能力：能看出每次都怎么拼错的。

    只存 user_progress 的话，这个功能根本做不出来。
    """
    client, headers, _ = auth_client
    now = datetime.now(UTC)
    w = words[0]

    for i, wrong in enumerate(["accomodate", "acommodate", "accommadate"]):
        await _answer(client, headers, w.id, "dictation", wrong, now + timedelta(minutes=i))

    body = (await client.get("/api/progress/wrong-words", headers=headers)).json()
    inputs = body["items"][0]["recentInputs"]

    assert len(inputs) == 3
    assert set(inputs) == {"accomodate", "acommodate", "accommadate"}
    assert inputs[0] == "accommadate", "最近的错误输入应排在最前"


async def test_wrong_words_filter_by_mode(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)

    await _answer(client, headers, words[0].id, "dictation", "wrong", now)
    await _answer(client, headers, words[1].id, "recognition", "unknown", now)

    dict_only = (
        await client.get(
            "/api/progress/wrong-words", headers=headers, params={"mode": "dictation"}
        )
    ).json()
    assert dict_only["total"] == 1
    assert dict_only["items"][0]["mode"] == "dictation"

    both = (await client.get("/api/progress/wrong-words", headers=headers)).json()
    assert both["total"] == 2


async def test_wrong_words_pagination(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)
    for i, w in enumerate(words[:4]):
        await _answer(client, headers, w.id, "dictation", f"wrong-{i}", now)

    page = (
        await client.get(
            "/api/progress/wrong-words", headers=headers, params={"limit": 2, "offset": 0}
        )
    ).json()
    assert page["total"] == 4
    assert len(page["items"]) == 2
    assert page["limit"] == 2

    page2 = (
        await client.get(
            "/api/progress/wrong-words", headers=headers, params={"limit": 2, "offset": 2}
        )
    ).json()
    assert len(page2["items"]) == 2
    ids1 = {i["wordId"] for i in page["items"]}
    ids2 = {i["wordId"] for i in page2["items"]}
    assert not (ids1 & ids2), "分页有重叠"


async def test_wrong_words_only_shows_own_data(auth_client, words, db_session):
    """⭐ 不能看到别人的错题。"""
    client, headers, _ = auth_client
    other = User(
        username=f"_other_{uuid.uuid4().hex[:8]}",
        nickname="别人",
        password_hash=hash_password("x"),
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        AnswerEvent(
            user_id=other.id,
            word_id=words[0].id,
            mode=PracticeMode.DICTATION,
            is_correct=False,
            user_input="other-user-mistake",
            answered_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    body = (await client.get("/api/progress/wrong-words", headers=headers)).json()
    assert body["total"] == 0
    assert "other-user-mistake" not in str(body)

    await db_session.execute(delete(User).where(User.id == other.id))
    await db_session.commit()


# ─────────────────────────────────────────────────────────────
# ⭐⭐ 重建 —— ADR-002 的核心承诺
# ─────────────────────────────────────────────────────────────

async def test_rebuild_restores_deleted_progress(auth_client, words, db_session):
    """⭐⭐ 本项目最重要的一条测试。

    **删掉整个 user_progress，从 answer_events 完整重建。**

    这验证的是 ADR-002 的核心承诺：进度表是缓存，事件表才是事实来源。
    这条挂了，说明混合式事件溯源的设计已经破了。
    """
    client, headers, user = auth_client
    now = datetime.now(UTC)

    # 造一串有对有错的答题历史
    plan = [
        (words[0], True, 0), (words[0], True, 48), (words[0], False, 120),
        (words[1], True, 0), (words[1], True, 24), (words[1], True, 72),
        (words[2], False, 10),
    ]
    for word, correct, hours in plan:
        await _answer(
            client, headers, word.id, "dictation",
            word.word if correct else "wrong-input",
            now + timedelta(hours=hours),
        )

    # 记下重建前的状态
    before = {
        (p.word_id, p.mode): (p.box, p.correct_count, p.wrong_count, p.next_review_at)
        for p in (
            await db_session.execute(
                select(UserProgress).where(UserProgress.user_id == user.id)
            )
        ).scalars()
    }
    assert len(before) == 3

    # ⭐ 把进度表整个删掉
    await db_session.execute(delete(UserProgress).where(UserProgress.user_id == user.id))
    await db_session.commit()

    remaining = (
        await db_session.execute(
            select(func.count()).select_from(UserProgress).where(
                UserProgress.user_id == user.id
            )
        )
    ).scalar_one()
    assert remaining == 0, "没删干净"

    # ⭐ 从事件重建
    r = await client.post("/api/progress/rebuild", headers=headers, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rebuilt"] == 3
    assert body["eventsRead"] == len(plan)

    after = {
        (p.word_id, p.mode): (p.box, p.correct_count, p.wrong_count, p.next_review_at)
        for p in (
            await db_session.execute(
                select(UserProgress).where(UserProgress.user_id == user.id)
            )
        ).scalars()
    }

    assert after == before, "重建结果与原状态不一致 —— 事件溯源的承诺被破坏了"


async def test_rebuild_removes_orphan_progress(auth_client, words, db_session):
    """有进度行但没有对应事件 → 是脏数据，重建时清掉。"""
    client, headers, user = auth_client

    db_session.add(
        UserProgress(
            user_id=user.id,
            word_id=words[0].id,
            mode=PracticeMode.DICTATION,
            box=3,
            next_review_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    body = (await client.post("/api/progress/rebuild", headers=headers, json={})).json()
    assert body["removed"] == 1
    assert body["rebuilt"] == 0

    left = (
        await db_session.execute(
            select(func.count()).select_from(UserProgress).where(
                UserProgress.user_id == user.id
            )
        )
    ).scalar_one()
    assert left == 0


async def test_rebuild_single_word(auth_client, words, db_session):
    """只重建指定的词，不动其他的。"""
    client, headers, user = auth_client
    now = datetime.now(UTC)

    await _answer(client, headers, words[0].id, "dictation", words[0].word, now)
    await _answer(client, headers, words[1].id, "dictation", words[1].word, now)

    # 手工把 words[1] 的进度改脏
    p1 = (
        await db_session.execute(
            select(UserProgress).where(
                UserProgress.user_id == user.id, UserProgress.word_id == words[1].id
            )
        )
    ).scalar_one()
    p1.box = 5
    await db_session.commit()

    # 只重建 words[0]
    body = (
        await client.post(
            "/api/progress/rebuild", headers=headers, json={"wordId": str(words[0].id)}
        )
    ).json()
    assert body["rebuilt"] == 1

    await db_session.refresh(p1)
    assert p1.box == 5, "没指定的词不该被动"


async def test_rebuild_is_idempotent(auth_client, words, db_session):
    """重建两次结果一样。"""
    client, headers, user = auth_client
    now = datetime.now(UTC)
    await _answer(client, headers, words[0].id, "dictation", words[0].word, now)
    await _answer(client, headers, words[0].id, "dictation", "wrong", now + timedelta(hours=1))

    first = (await client.post("/api/progress/rebuild", headers=headers, json={})).json()
    snapshot = (
        await db_session.execute(
            select(UserProgress.box, UserProgress.correct_count, UserProgress.wrong_count)
            .where(UserProgress.user_id == user.id)
        )
    ).all()

    second = (await client.post("/api/progress/rebuild", headers=headers, json={})).json()
    again = (
        await db_session.execute(
            select(UserProgress.box, UserProgress.correct_count, UserProgress.wrong_count)
            .where(UserProgress.user_id == user.id)
        )
    ).all()

    assert first["rebuilt"] == second["rebuilt"]
    assert snapshot == again


async def test_rebuild_empty_user(auth_client):
    client, headers, _ = auth_client
    body = (await client.post("/api/progress/rebuild", headers=headers, json={})).json()
    assert body == {"rebuilt": 0, "removed": 0, "eventsRead": 0, "durationMs": body["durationMs"]}


async def test_rebuild_does_not_touch_other_users(auth_client, words, db_session):
    """⭐ 重建只影响自己的数据。"""
    client, headers, _ = auth_client
    other = User(
        username=f"_other_{uuid.uuid4().hex[:8]}",
        nickname="别人",
        password_hash=hash_password("x"),
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        UserProgress(
            user_id=other.id,
            word_id=words[0].id,
            mode=PracticeMode.DICTATION,
            box=4,
            next_review_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    await client.post("/api/progress/rebuild", headers=headers, json={})

    still = (
        await db_session.execute(
            select(UserProgress.box).where(UserProgress.user_id == other.id)
        )
    ).scalar_one_or_none()
    assert still == 4, "别人的进度被动了"

    await db_session.execute(delete(User).where(User.id == other.id))
    await db_session.commit()
