"""测试模式与标记 —— 服务层。

═══════════════════════════════════════════════════════════════════════
测试模式与学习模式的区别（ADR-013）
═══════════════════════════════════════════════════════════════════════

    学习（每日任务 / 自由练习）   测试
    ─────────────────────────   ────────────────────────
    错词进 Leitner 循环重现       "错了就是错了"，不重现
    影响 box 与复习排期           **不影响进度**
    可以"判我对"更正              **不能更正**（否则成绩没意义）
    —                            出成绩、留历史记录

实现上：测试答题照常写进 `answer_events`，但带 `is_test=true` 与
`test_session_id`，回放时跳过（见 services/replay.py）。
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AnswerEvent,
    PracticeMode,
    TestSession,
    User,
    UserProgress,
    Word,
    WordStar,
)
from app.models.enums import MAX_BOX, MIN_BOX
from app.services.distractor import Candidate, build_question
from app.services.practice import PracticeItem, PracticeSet, _load_candidate_pool

__all__ = [
    "TestScope",
    "TestSummary",
    "correct_answer_event",
    "list_starred",
    "list_test_sessions",
    "star_word",
    "start_test",
    "test_detail",
    "unstar_word",
]

#: 测试出题范围
TestScope = str   # learned | all | topic | box

VALID_SCOPES = frozenset({"learned", "all", "topic", "box"})


@dataclass(frozen=True, slots=True)
class TestSummary:
    """一次测试的成绩单。得分从事件现算，不冗余存。"""

    session: TestSession
    answered: int
    correct: int

    @property
    def score(self) -> float:
        """百分制得分。未答完时按已答题数算。"""
        return round(self.correct / self.answered * 100, 1) if self.answered else 0.0

    @property
    def is_complete(self) -> bool:
        return self.answered >= self.session.total


# ─────────────────────────────────────────────────────────────
# 开始测试
# ─────────────────────────────────────────────────────────────


async def _pick_test_words(
    db: AsyncSession,
    user_id: uuid.UUID,
    mode: PracticeMode,
    word_list_id: uuid.UUID,
    scope: TestScope,
    scope_value: str | None,
    count: int,
) -> list[Word]:
    """按范围挑出考题。一律随机排序 —— 测试不该有固定顺序。"""
    stmt = select(Word).where(Word.word_list_id == word_list_id)

    if scope == "learned":
        # 只考有进度记录的词 —— 测的是"我学过的记住了吗"
        learned = (
            select(UserProgress.word_id)
            .where(UserProgress.user_id == user_id, UserProgress.mode == mode)
            .scalar_subquery()
        )
        stmt = stmt.where(Word.id.in_(learned))

    elif scope == "topic":
        if not scope_value:
            raise ValueError("scope=topic 时必须指定话题")
        stmt = stmt.where(Word.topic == scope_value)

    elif scope == "box":
        if not scope_value or not scope_value.isdigit():
            raise ValueError("scope=box 时必须指定盒子号（1–5）")
        box = int(scope_value)
        if not MIN_BOX <= box <= MAX_BOX:
            raise ValueError(f"盒子号必须在 {MIN_BOX}–{MAX_BOX} 之间")
        in_box = (
            select(UserProgress.word_id)
            .where(
                UserProgress.user_id == user_id,
                UserProgress.mode == mode,
                UserProgress.box == box,
            )
            .scalar_subquery()
        )
        stmt = stmt.where(Word.id.in_(in_box))

    elif scope != "all":
        raise ValueError(f"未知的范围：{scope}（合法值 {sorted(VALID_SCOPES)}）")

    return list(
        (await db.execute(stmt.order_by(func.random()).limit(count))).scalars().all()
    )


async def start_test(
    db: AsyncSession,
    user: User,
    mode: PracticeMode,
    word_list_id: uuid.UUID,
    scope: TestScope,
    scope_value: str | None,
    count: int,
    rng: random.Random | None = None,
) -> tuple[TestSession, PracticeSet]:
    """开始一次测试 —— 建会话 + 出题。

    返回 (会话, 题目集)。客户端答题时要带上会话 id，
    服务端据此把这批答题标记为测试并归到同一次考试。
    """
    rng = rng or random.Random()

    words = await _pick_test_words(
        db, user.id, mode, word_list_id, scope, scope_value, count
    )
    if not words:
        raise ValueError("这个范围里没有可考的词 —— 换个范围，或先去学一些词")

    session = TestSession(
        user_id=user.id,
        mode=mode,
        scope=scope,
        scope_value=scope_value,
        total=len(words),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    pool = (
        await _load_candidate_pool(db, word_list_id)
        if mode is PracticeMode.RECOGNITION
        else []
    )

    items = []
    for word in words:
        question = None
        if mode is PracticeMode.RECOGNITION:
            target = Candidate(word.id, word.meaning_primary, word.topic, word.part_of_speech)
            question = build_question(target, pool, rng)
        # 测试模式不显示 box —— 免得暗示"这个词你很熟"
        items.append(PracticeItem(word=word, box=None, question=question))

    practice_set = PracticeSet(
        mode=mode,
        items=tuple(items),
        review_count=0,
        new_count=0,
    )
    return session, practice_set


async def get_test_session(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> TestSession | None:
    """取测试会话，并校验属于该用户。

    ⚠️ 必须校验归属 —— 否则客户端能编个别人的 session id，
       把真实练习伪装成"不计入进度"的测试。
    """
    return (
        await db.execute(
            select(TestSession).where(
                TestSession.id == session_id, TestSession.user_id == user_id
            )
        )
    ).scalar_one_or_none()


# ─────────────────────────────────────────────────────────────
# 测试记录
# ─────────────────────────────────────────────────────────────


async def _summaries_for(
    db: AsyncSession, sessions: list[TestSession]
) -> list[TestSummary]:
    """批量算成绩 —— 一次查询拿到所有会话的答题统计，避免 N+1。"""
    if not sessions:
        return []

    ids = [s.id for s in sessions]
    stats = {
        sid: (answered, correct)
        for sid, answered, correct in (
            await db.execute(
                select(
                    AnswerEvent.test_session_id,
                    func.count(),
                    func.count().filter(AnswerEvent.is_correct.is_(True)),
                )
                .where(AnswerEvent.test_session_id.in_(ids))
                .group_by(AnswerEvent.test_session_id)
            )
        ).all()
    }

    return [
        TestSummary(session=s, answered=stats.get(s.id, (0, 0))[0],
                    correct=stats.get(s.id, (0, 0))[1])
        for s in sessions
    ]


async def list_test_sessions(
    db: AsyncSession,
    user_id: uuid.UUID,
    mode: PracticeMode | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[TestSummary], int]:
    """测试历史，最近的在前。"""
    conditions = [TestSession.user_id == user_id]
    if mode is not None:
        conditions.append(TestSession.mode == mode)

    total = (
        await db.execute(
            select(func.count()).select_from(TestSession).where(*conditions)
        )
    ).scalar_one()

    sessions = list(
        (
            await db.execute(
                select(TestSession)
                .where(*conditions)
                .order_by(TestSession.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return await _summaries_for(db, sessions), total


async def test_detail(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[TestSummary, list[tuple[Word, AnswerEvent]]] | None:
    """单次测试详情：成绩 + 逐题记录。"""
    session = await get_test_session(db, user_id, session_id)
    if session is None:
        return None

    rows = (
        await db.execute(
            select(Word, AnswerEvent)
            .join(AnswerEvent, AnswerEvent.word_id == Word.id)
            .where(AnswerEvent.test_session_id == session_id)
            .order_by(AnswerEvent.answered_at)
        )
    ).all()

    summary = (await _summaries_for(db, [session]))[0]
    return summary, [(w, e) for w, e in rows]


# ─────────────────────────────────────────────────────────────
# 判我对（更正事件）
# ─────────────────────────────────────────────────────────────

#: "判我对"允许的最大编辑距离。
#: 限制的意义：typo（差一两个字母）可以自认掌握，
#: 完全不会的词不该能靠点一下按钮刷进度。
MAX_CORRECTION_DISTANCE = 2


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein 距离。只用于"判我对"的滥用防护，单词很短，性能无所谓。"""
    a, b = a.lower(), b.lower()
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                prev[j - 1] if ca == cb else 1 + min(prev[j - 1], prev[j], cur[j - 1])
            )
        prev = cur
    return prev[-1]


async def correct_answer_event(
    db: AsyncSession,
    user: User,
    event_id: int,
    answered_at: datetime,
) -> AnswerEvent:
    """把某次答错改判为对 —— 追加一条**更正事件**。

    ⚠️ 不修改原事件（ADR-002 铁律：事件表只追加）。
       回放时把被指向的事件视为答对（见 services/replay.py）。

    四道校验：
        1. 事件属于当前用户
        2. 原本确实答错了（答对的没什么可更正）
        3. **不是测试事件** —— 测试"错了就是错了"，能改成绩就没意义了
        4. 编辑距离 ≤ 2 —— 防止把完全不会的词也判成"我会"

    Raises:
        ValueError: 任一校验不过。调用方转成 400/403。
    """
    event = (
        await db.execute(select(AnswerEvent).where(AnswerEvent.id == event_id))
    ).scalar_one_or_none()

    if event is None or event.user_id != user.id:
        raise ValueError("找不到这条答题记录")
    if event.is_correct:
        raise ValueError("这次本来就答对了，不需要更正")
    if event.is_test:
        raise ValueError("测试模式不能判我对 —— 测试就是要如实反映水平")
    if event.corrects_event_id is not None:
        raise ValueError("这是一条更正记录，不能再更正")

    already = (
        await db.execute(
            select(AnswerEvent.id).where(AnswerEvent.corrects_event_id == event_id)
        )
    ).scalar_one_or_none()
    if already is not None:
        raise ValueError("这次答题已经更正过了")

    # 只有听写模式需要算编辑距离；阅读模式选错就是选错，不给更正
    if event.mode is not PracticeMode.RECOGNITION:
        word = (
            await db.execute(select(Word).where(Word.id == event.word_id))
        ).scalar_one()
        distance = _edit_distance(event.user_input or "", word.word)
        if distance > MAX_CORRECTION_DISTANCE:
            raise ValueError(
                f"差了 {distance} 个字母，超出「拼写失误」的范围"
                f"（最多 {MAX_CORRECTION_DISTANCE} 个）"
            )
    else:
        raise ValueError("阅读模式选错就是选错，不支持判我对")

    correction = AnswerEvent(
        user_id=user.id,
        word_id=event.word_id,
        mode=event.mode,
        is_correct=True,
        user_input=None,
        answered_at=answered_at,
        corrects_event_id=event.id,
    )
    db.add(correction)
    await db.commit()
    await db.refresh(correction)
    return correction


# ─────────────────────────────────────────────────────────────
# 星标
# ─────────────────────────────────────────────────────────────


async def star_word(
    db: AsyncSession, user_id: uuid.UUID, word_id: uuid.UUID, note: str | None = None
) -> WordStar:
    """加星标。重复加只更新备注，不报错（幂等）。"""
    existing = (
        await db.execute(
            select(WordStar).where(
                WordStar.user_id == user_id, WordStar.word_id == word_id
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if note is not None:
            existing.note = note
            await db.commit()
        return existing

    star = WordStar(user_id=user_id, word_id=word_id, note=note)
    db.add(star)
    await db.commit()
    await db.refresh(star)
    return star


async def unstar_word(db: AsyncSession, user_id: uuid.UUID, word_id: uuid.UUID) -> bool:
    """取消星标。返回是否真的删了（没标过也不报错）。"""
    result = await db.execute(
        delete(WordStar).where(WordStar.user_id == user_id, WordStar.word_id == word_id)
    )
    await db.commit()
    return (result.rowcount or 0) > 0


async def list_starred(
    db: AsyncSession, user_id: uuid.UUID, limit: int = 100, offset: int = 0
) -> tuple[list[tuple[Word, WordStar]], int]:
    """星标列表，最近标的在前。"""
    total = (
        await db.execute(
            select(func.count()).select_from(WordStar).where(WordStar.user_id == user_id)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(Word, WordStar)
            .join(WordStar, WordStar.word_id == Word.id)
            .where(WordStar.user_id == user_id)
            .order_by(WordStar.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [(w, s) for w, s in rows], total
