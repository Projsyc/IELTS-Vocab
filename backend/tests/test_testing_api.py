"""测试模式与标记的接口测试（ADR-013）。

⭐ 核心不变式：**测试模式的答题绝不影响 Leitner 进度。**
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.core.security import hash_password
from app.models import AnswerEvent, PracticeMode, User, UserProgress, Word, WordStar


@pytest_asyncio.fixture
async def auth_client(client, db_session) -> AsyncGenerator[tuple, None]:
    password = "test-mode-pw"
    user = User(
        username=f"_tm_{uuid.uuid4().hex[:8]}",
        nickname="测试模式",
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
    rows = list((await db_session.execute(select(Word).limit(10))).scalars().all())
    if len(rows) < 10:
        pytest.skip("词库未导入，先跑 pnpm seed")
    return rows


async def _answer(client, headers, word_id, mode, user_input, at, session_id=None):
    body = {
        "wordId": str(word_id),
        "mode": mode,
        "userInput": user_input,
        "answeredAt": at.isoformat(),
    }
    if session_id:
        body["testSessionId"] = str(session_id)
    return await client.post("/api/practice/answer", headers=headers, json=body)


# ─────────────────────────────────────────────────────────────
# 鉴权
# ─────────────────────────────────────────────────────────────

async def test_start_test_requires_auth(client):
    r = await client.post("/api/practice/test", json={"mode": "dictation"})
    assert r.status_code == 401


async def test_star_requires_auth(client):
    r = await client.put(f"/api/words/{uuid.uuid4()}/star", json={})
    assert r.status_code == 401


# ─────────────────────────────────────────────────────────────
# 开始测试
# ─────────────────────────────────────────────────────────────

async def test_start_test_all_scope(auth_client, words):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/test",
        headers=headers,
        json={"mode": "dictation", "count": 8, "scope": "all"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 8
    assert len(body["items"]) == 8
    assert body["testSessionId"]
    assert all(i["box"] is None for i in body["items"]), "测试不该显示盒子号"


async def test_start_test_learned_scope_empty(auth_client, words):
    """新用户没学过任何词 → learned 范围为空，应给出清楚的 400。"""
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/test",
        headers=headers,
        json={"mode": "dictation", "count": 5, "scope": "learned"},
    )
    assert r.status_code == 400
    assert "没有可考的词" in r.json()["detail"]


async def test_start_test_learned_scope_after_learning(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)
    for w in words[:3]:
        await _answer(client, headers, w.id, "dictation", w.word, now)

    r = await client.post(
        "/api/practice/test",
        headers=headers,
        json={"mode": "dictation", "count": 10, "scope": "learned"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3, "只有 3 个词学过"
    learned_ids = {str(w.id) for w in words[:3]}
    assert {i["wordId"] for i in body["items"]} == learned_ids


async def test_start_test_topic_scope(auth_client, words):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/test",
        headers=headers,
        json={"mode": "recognition", "count": 5, "scope": "topic", "scopeValue": "教育"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] > 0


async def test_start_test_box_scope(auth_client, words):
    client, headers, user = auth_client
    r = await client.post(
        "/api/practice/test",
        headers=headers,
        json={"mode": "dictation", "count": 5, "scope": "box", "scopeValue": "3"},
    )
    # 没有 Box 3 的词 → 400
    assert r.status_code == 400


@pytest.mark.parametrize("bad", [
    {"mode": "dictation", "scope": "topic"},                       # 缺 scopeValue
    {"mode": "dictation", "scope": "box"},                         # 缺 scopeValue
    {"mode": "dictation", "scope": "box", "scopeValue": "9"},      # 盒子号越界
])
async def test_start_test_validation(auth_client, bad):
    client, headers, _ = auth_client
    r = await client.post("/api/practice/test", headers=headers, json=bad)
    assert r.status_code == 400


async def test_start_test_rejects_bad_scope(auth_client):
    client, headers, _ = auth_client
    r = await client.post(
        "/api/practice/test", headers=headers, json={"mode": "dictation", "scope": "bogus"}
    )
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────
# ⭐⭐ 测试答题不影响进度
# ─────────────────────────────────────────────────────────────

async def test_test_answers_do_not_affect_progress(auth_client, words, db_session):
    """⭐⭐ ADR-013 的核心不变式。

    先正常学到 Box 3，再在测试里全答错 —— 进度必须纹丝不动。
    """
    client, headers, user = auth_client
    now = datetime.now(UTC)
    w = words[0]

    # 正常学：连对 2 次 → Box 3
    await _answer(client, headers, w.id, "dictation", w.word, now)
    await _answer(client, headers, w.id, "dictation", w.word, now + timedelta(hours=1))

    before = (
        await db_session.execute(
            select(UserProgress).where(
                UserProgress.user_id == user.id,
                UserProgress.word_id == w.id,
                UserProgress.mode == PracticeMode.DICTATION,
            )
        )
    ).scalar_one()
    assert before.box == 3
    snapshot = (before.box, before.correct_count, before.wrong_count, before.next_review_at)

    # 测试里答错
    session_id = (
        await client.post(
            "/api/practice/test",
            headers=headers,
            json={"mode": "dictation", "count": 1, "scope": "learned"},
        )
    ).json()["testSessionId"]

    r = await _answer(
        client, headers, w.id, "dictation", "definitely-wrong",
        now + timedelta(hours=2), session_id,
    )
    assert r.status_code == 200, r.text
    assert r.json()["isCorrect"] is False
    assert r.json()["progress"] is None, "测试模式不该返回进度"

    await db_session.refresh(before)
    after = (before.box, before.correct_count, before.wrong_count, before.next_review_at)
    assert after == snapshot, "⭐ 测试答错改动了 Leitner 进度 —— ADR-013 被破坏"


async def test_test_answer_is_recorded_as_event(auth_client, words, db_session):
    """测试答题仍然写事件 —— 要出成绩、要进错题本。"""
    client, headers, user = auth_client
    now = datetime.now(UTC)
    w = words[0]

    session_id = (
        await client.post(
            "/api/practice/test",
            headers=headers,
            json={"mode": "dictation", "count": 1, "scope": "all"},
        )
    ).json()["testSessionId"]

    await _answer(client, headers, w.id, "dictation", "wrong", now, session_id)

    event = (
        await db_session.execute(
            select(AnswerEvent).where(AnswerEvent.user_id == user.id)
        )
    ).scalar_one()
    assert event.is_test is True
    assert str(event.test_session_id) == session_id
    assert event.user_input == "wrong"


async def test_test_events_excluded_from_rebuild(auth_client, words, db_session):
    """⭐ 全量重建时也必须跳过测试事件。"""
    client, headers, user = auth_client
    now = datetime.now(UTC)
    w = words[0]

    await _answer(client, headers, w.id, "dictation", w.word, now)

    session_id = (
        await client.post(
            "/api/practice/test", headers=headers,
            json={"mode": "dictation", "count": 1, "scope": "all"},
        )
    ).json()["testSessionId"]
    await _answer(
        client, headers, w.id, "dictation", "wrong", now + timedelta(hours=1), session_id
    )

    await client.post("/api/progress/rebuild", headers=headers, json={})

    progress = (
        await db_session.execute(
            select(UserProgress).where(
                UserProgress.user_id == user.id, UserProgress.word_id == w.id
            )
        )
    ).scalar_one()
    assert progress.box == 2, "重建后应只反映那一次正常答对"
    assert progress.wrong_count == 0


async def test_answer_rejects_foreign_test_session(auth_client, words, db_session):
    """⭐ 不能用别人的 session id —— 否则能把真实练习伪装成测试来逃避降箱。"""
    client, headers, _ = auth_client
    other = User(
        username=f"_other_{uuid.uuid4().hex[:8]}",
        nickname="别人",
        password_hash=hash_password("x"),
    )
    db_session.add(other)
    await db_session.commit()

    # 用别人的账号建测试会话
    other_login = await client.post(
        "/api/auth/login", json={"username": other.username, "password": "x"}
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['accessToken']}"}
    other_session = (
        await client.post(
            "/api/practice/test", headers=other_headers,
            json={"mode": "dictation", "count": 1, "scope": "all"},
        )
    ).json()["testSessionId"]

    r = await _answer(
        client, headers, words[0].id, "dictation", "x", datetime.now(UTC), other_session
    )
    assert r.status_code == 404

    await db_session.execute(delete(User).where(User.id == other.id))
    await db_session.commit()


async def test_answer_rejects_mode_mismatch(auth_client, words):
    """测试是听写模式，却提交阅读答案 → 400。"""
    client, headers, _ = auth_client
    session_id = (
        await client.post(
            "/api/practice/test", headers=headers,
            json={"mode": "dictation", "count": 1, "scope": "all"},
        )
    ).json()["testSessionId"]

    r = await _answer(
        client, headers, words[0].id, "recognition", "x", datetime.now(UTC), session_id
    )
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────────
# 测试记录
# ─────────────────────────────────────────────────────────────

async def test_test_history_and_score(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)

    start = (
        await client.post(
            "/api/practice/test", headers=headers,
            json={"mode": "dictation", "count": 4, "scope": "all"},
        )
    ).json()
    sid = start["testSessionId"]

    # 4 题答对 3 个
    for i, item in enumerate(start["items"]):
        correct = i < 3
        await _answer(
            client, headers, item["wordId"], "dictation",
            item["word"] if correct else "wrong",
            now + timedelta(minutes=i), sid,
        )

    page = (await client.get("/api/progress/tests", headers=headers)).json()
    assert page["total"] == 1
    s = page["items"][0]
    assert s["total"] == 4
    assert s["answered"] == 4
    assert s["correct"] == 3
    assert s["score"] == 75.0
    assert s["isComplete"] is True


async def test_test_detail(auth_client, words):
    client, headers, _ = auth_client
    now = datetime.now(UTC)
    start = (
        await client.post(
            "/api/practice/test", headers=headers,
            json={"mode": "dictation", "count": 2, "scope": "all"},
        )
    ).json()
    sid = start["testSessionId"]
    for item in start["items"]:
        await _answer(client, headers, item["wordId"], "dictation", "wrong", now, sid)

    detail = (await client.get(f"/api/progress/tests/{sid}", headers=headers)).json()
    assert detail["summary"]["correct"] == 0
    assert len(detail["answers"]) == 2
    assert all(a["isCorrect"] is False for a in detail["answers"])
    assert all(a["userInput"] == "wrong" for a in detail["answers"])


async def test_test_detail_404_for_other_user(auth_client):
    client, headers, _ = auth_client
    r = await client.get(f"/api/progress/tests/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404


async def test_incomplete_test_shows_partial_score(auth_client, words):
    """只答了一半也能看成绩，标记为未完成。"""
    client, headers, _ = auth_client
    start = (
        await client.post(
            "/api/practice/test", headers=headers,
            json={"mode": "dictation", "count": 4, "scope": "all"},
        )
    ).json()
    sid = start["testSessionId"]
    await _answer(
        client, headers, start["items"][0]["wordId"], "dictation",
        start["items"][0]["word"], datetime.now(UTC), sid,
    )

    s = (await client.get("/api/progress/tests", headers=headers)).json()["items"][0]
    assert s["answered"] == 1
    assert s["score"] == 100.0
    assert s["isComplete"] is False


# ─────────────────────────────────────────────────────────────
# ⭐ 判我对
# ─────────────────────────────────────────────────────────────

async def test_correct_restores_progress(auth_client, words, db_session):
    """⭐ typo 把 Box 5 的词打回 Box 1，判我对后恢复。"""
    client, headers, user = auth_client
    now = datetime.now(UTC)
    w = words[0]

    # 连对 4 次 → Box 5
    for i in range(4):
        await _answer(client, headers, w.id, "dictation", w.word, now + timedelta(hours=i))

    # typo（改一个字母）→ Box 1
    typo = w.word[:-1] + ("z" if not w.word.endswith("z") else "y")
    r = await _answer(client, headers, w.id, "dictation", typo, now + timedelta(hours=10))
    assert r.json()["progress"]["box"] == 1
    event_id = r.json()["eventId"]

    # 判我对
    fix = await client.post(
        "/api/practice/correct",
        headers=headers,
        json={"eventId": event_id, "answeredAt": (now + timedelta(hours=11)).isoformat()},
    )
    assert fix.status_code == 200, fix.text
    assert fix.json()["progress"]["box"] == 5, "更正后应恢复 Box 5"
    assert fix.json()["progress"]["wrongCount"] == 0


async def test_correct_rejects_far_spelling(auth_client, words):
    """⭐ 编辑距离太大不给更正 —— 防止把完全不会的词也判成"我会"。"""
    client, headers, _ = auth_client
    w = words[0]
    r = await _answer(
        client, headers, w.id, "dictation", "totally-different-nonsense", datetime.now(UTC)
    )
    event_id = r.json()["eventId"]

    fix = await client.post(
        "/api/practice/correct",
        headers=headers,
        json={"eventId": event_id, "answeredAt": datetime.now(UTC).isoformat()},
    )
    assert fix.status_code == 400
    assert "拼写失误" in fix.json()["detail"]


async def test_correct_rejects_test_event(auth_client, words):
    """⭐ 测试模式不能判我对 —— 否则成绩没意义了。"""
    client, headers, _ = auth_client
    w = words[0]
    sid = (
        await client.post(
            "/api/practice/test", headers=headers,
            json={"mode": "dictation", "count": 1, "scope": "all"},
        )
    ).json()["testSessionId"]

    r = await _answer(
        client, headers, w.id, "dictation", w.word[:-1], datetime.now(UTC), sid
    )
    fix = await client.post(
        "/api/practice/correct",
        headers=headers,
        json={"eventId": r.json()["eventId"], "answeredAt": datetime.now(UTC).isoformat()},
    )
    assert fix.status_code == 400
    assert "测试模式" in fix.json()["detail"]


async def test_correct_rejects_already_correct(auth_client, words):
    client, headers, _ = auth_client
    w = words[0]
    r = await _answer(client, headers, w.id, "dictation", w.word, datetime.now(UTC))
    fix = await client.post(
        "/api/practice/correct",
        headers=headers,
        json={"eventId": r.json()["eventId"], "answeredAt": datetime.now(UTC).isoformat()},
    )
    assert fix.status_code == 400
    assert "本来就答对" in fix.json()["detail"]


async def test_correct_rejects_double_correction(auth_client, words):
    client, headers, _ = auth_client
    w = words[0]
    r = await _answer(
        client, headers, w.id, "dictation", w.word[:-1], datetime.now(UTC)
    )
    body = {"eventId": r.json()["eventId"], "answeredAt": datetime.now(UTC).isoformat()}
    assert (await client.post("/api/practice/correct", headers=headers, json=body)).status_code == 200
    again = await client.post("/api/practice/correct", headers=headers, json=body)
    assert again.status_code == 400
    assert "已经更正过" in again.json()["detail"]


async def test_correct_rejects_other_users_event(auth_client, words, db_session):
    client, headers, _ = auth_client
    other = User(
        username=f"_o_{uuid.uuid4().hex[:8]}", nickname="别人",
        password_hash=hash_password("x"),
    )
    db_session.add(other)
    await db_session.flush()
    event = AnswerEvent(
        user_id=other.id, word_id=words[0].id, mode=PracticeMode.DICTATION,
        is_correct=False, user_input="x", answered_at=datetime.now(UTC),
    )
    db_session.add(event)
    await db_session.commit()

    fix = await client.post(
        "/api/practice/correct", headers=headers,
        json={"eventId": event.id, "answeredAt": datetime.now(UTC).isoformat()},
    )
    assert fix.status_code == 400

    await db_session.execute(delete(User).where(User.id == other.id))
    await db_session.commit()


async def test_correct_rejects_recognition_mode(auth_client, words):
    """阅读模式选错就是选错。"""
    client, headers, _ = auth_client
    r = await _answer(
        client, headers, words[0].id, "recognition", "unknown", datetime.now(UTC)
    )
    fix = await client.post(
        "/api/practice/correct", headers=headers,
        json={"eventId": r.json()["eventId"], "answeredAt": datetime.now(UTC).isoformat()},
    )
    assert fix.status_code == 400
    assert "阅读模式" in fix.json()["detail"]


async def test_correction_survives_rebuild(auth_client, words, db_session):
    """⭐ 更正后再全量重建，结果不变 —— 证明更正是真正的事件。"""
    client, headers, user = auth_client
    now = datetime.now(UTC)
    w = words[0]

    for i in range(3):
        await _answer(client, headers, w.id, "dictation", w.word, now + timedelta(hours=i))
    typo = w.word[:-1] + ("z" if not w.word.endswith("z") else "y")
    r = await _answer(client, headers, w.id, "dictation", typo, now + timedelta(hours=5))
    await client.post(
        "/api/practice/correct", headers=headers,
        json={"eventId": r.json()["eventId"], "answeredAt": (now + timedelta(hours=6)).isoformat()},
    )

    p = (
        await db_session.execute(
            select(UserProgress).where(
                UserProgress.user_id == user.id, UserProgress.word_id == w.id
            )
        )
    ).scalar_one()
    before = (p.box, p.correct_count, p.wrong_count)

    await client.post("/api/progress/rebuild", headers=headers, json={})
    await db_session.refresh(p)
    assert (p.box, p.correct_count, p.wrong_count) == before


# ─────────────────────────────────────────────────────────────
# 星标
# ─────────────────────────────────────────────────────────────

async def test_star_and_list(auth_client, words):
    client, headers, _ = auth_client
    w = words[0]

    r = await client.put(f"/api/words/{w.id}/star", headers=headers, json={"note": "老记不住"})
    assert r.status_code == 204

    page = (await client.get("/api/words/starred", headers=headers)).json()
    assert page["total"] == 1
    assert page["items"][0]["wordId"] == str(w.id)
    assert page["items"][0]["note"] == "老记不住"


async def test_star_is_idempotent(auth_client, words):
    client, headers, _ = auth_client
    w = words[0]
    await client.put(f"/api/words/{w.id}/star", headers=headers, json={})
    await client.put(f"/api/words/{w.id}/star", headers=headers, json={"note": "改了备注"})

    page = (await client.get("/api/words/starred", headers=headers)).json()
    assert page["total"] == 1, "重复加星不该产生两条"
    assert page["items"][0]["note"] == "改了备注"


async def test_unstar(auth_client, words):
    client, headers, _ = auth_client
    w = words[0]
    await client.put(f"/api/words/{w.id}/star", headers=headers, json={})
    r = await client.delete(f"/api/words/{w.id}/star", headers=headers)
    assert r.status_code == 204
    assert (await client.get("/api/words/starred", headers=headers)).json()["total"] == 0


async def test_unstar_nonexistent_is_ok(auth_client, words):
    """没标过也返回 204 —— 幂等，前端不必先查再删。"""
    client, headers, _ = auth_client
    r = await client.delete(f"/api/words/{words[0].id}/star", headers=headers)
    assert r.status_code == 204


async def test_star_404_for_unknown_word(auth_client):
    client, headers, _ = auth_client
    r = await client.put(f"/api/words/{uuid.uuid4()}/star", headers=headers, json={})
    assert r.status_code == 404


async def test_stars_survive_rebuild(auth_client, words, db_session):
    """⭐ 星标不是从事件算出来的，重建进度不该把它抹掉。

    这正是"星标为什么单独一张表"的理由 —— 塞进 user_progress 就会被 rebuild 清掉。
    """
    client, headers, user = auth_client
    await client.put(f"/api/words/{words[0].id}/star", headers=headers, json={})

    await client.post("/api/progress/rebuild", headers=headers, json={})

    count = (
        await db_session.execute(
            select(func.count()).select_from(WordStar).where(WordStar.user_id == user.id)
        )
    ).scalar_one()
    assert count == 1, "重建进度把星标抹掉了 —— 它不该存在 user_progress 里"


async def test_stars_only_show_own(auth_client, words, db_session):
    client, headers, _ = auth_client
    other = User(
        username=f"_o_{uuid.uuid4().hex[:8]}", nickname="别人",
        password_hash=hash_password("x"),
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(WordStar(user_id=other.id, word_id=words[0].id, note="别人的"))
    await db_session.commit()

    page = (await client.get("/api/words/starred", headers=headers)).json()
    assert page["total"] == 0

    await db_session.execute(delete(User).where(User.id == other.id))
    await db_session.commit()
