"""练习相关的业务逻辑 —— 挑词、出题、判定、落库。

按项目约定，本层不 import FastAPI 的东西。
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerEvent, PracticeMode, User, UserProgress, Word, WordList
from app.services.dictation import DictationResult, judge_dictation
from app.services.distractor import Candidate, Question, build_question, strip_pos_prefix
from app.services.replay import AnswerRecord, ProgressSnapshot, replay, replay_incremental

__all__ = [
    "AnswerOutcome",
    "PracticeItem",
    "PracticeSet",
    "UNKNOWN_ANSWER",
    "build_daily_set",
    "build_free_set",
    "submit_answer",
]

#: 阅读模式下用户点"不知道"时提交的值。等同答错，但数据上可区分 ——
#: 主动承认不会 ≠ 蒙错了，将来做学习分析能用上。
UNKNOWN_ANSWER = "unknown"


@dataclass(frozen=True, slots=True)
class PracticeItem:
    """一道待练习的题。"""

    word: Word
    box: int | None                 # 当前盒子号，None 表示新词
    question: Question | None       # 仅阅读模式有值


@dataclass(frozen=True, slots=True)
class PracticeSet:
    mode: PracticeMode
    items: tuple[PracticeItem, ...]
    review_count: int
    new_count: int

    @property
    def total(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class AnswerOutcome:
    """一次答题的完整结果。"""

    is_correct: bool
    correct_answer: str
    dictation: DictationResult | None      # 仅听写模式
    #: 更新后的进度。**测试模式为 None** —— 测试不影响进度（ADR-013）
    progress: ProgressSnapshot | None
    was_replayed: bool                     # True 表示走了全量回放而非增量
    #: 刚写入的事件 id。前端要用它来"判我对"
    event_id: int


# ─────────────────────────────────────────────────────────────
# 候选池（出阅读题用）
# ─────────────────────────────────────────────────────────────


async def _load_candidate_pool(db: AsyncSession, word_list_id: uuid.UUID) -> list[Candidate]:
    """加载整个词库作为干扰项候选池。

    4,768 词一次性拉进内存约几 MB，比"每道题查一次库"快得多，
    也让降级链能在纯函数里完成（好测）。
    词库变动不频繁，将来量大了可以加缓存。
    """
    rows = (
        await db.execute(
            select(Word.id, Word.meaning_primary, Word.topic, Word.part_of_speech)
            .where(Word.word_list_id == word_list_id)
        )
    ).all()
    return [Candidate(wid, meaning, topic, pos) for wid, meaning, topic, pos in rows]


# ─────────────────────────────────────────────────────────────
# 挑词
# ─────────────────────────────────────────────────────────────


async def _due_words(
    db: AsyncSession,
    user_id: uuid.UUID,
    mode: PracticeMode,
    word_list_id: uuid.UUID,
    now: datetime,
    limit: int,
) -> list[tuple[Word, int]]:
    """到期该复习的词，按到期时间升序（最该复习的排前面）。"""
    rows = (
        await db.execute(
            select(Word, UserProgress.box)
            .join(UserProgress, UserProgress.word_id == Word.id)
            .where(
                UserProgress.user_id == user_id,
                UserProgress.mode == mode,
                UserProgress.next_review_at <= now,
                Word.word_list_id == word_list_id,
            )
            .order_by(UserProgress.next_review_at)
            .limit(limit)
        )
    ).all()
    return [(w, box) for w, box in rows]


async def _new_words(
    db: AsyncSession,
    user_id: uuid.UUID,
    mode: PracticeMode,
    word_list_id: uuid.UUID,
    limit: int,
) -> list[Word]:
    """没有进度记录的词 = 新词。按词频排，先学高频的。"""
    if limit <= 0:
        return []

    seen = (
        select(UserProgress.word_id)
        .where(UserProgress.user_id == user_id, UserProgress.mode == mode)
        .scalar_subquery()
    )
    return list(
        (
            await db.execute(
                select(Word)
                .where(Word.word_list_id == word_list_id, Word.id.notin_(seen))
                .order_by(Word.frq.nulls_last(), Word.word)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


def _assemble(
    mode: PracticeMode,
    review: list[tuple[Word, int]],
    new: list[Word],
    pool: list[Candidate],
    rng: random.Random,
) -> PracticeSet:
    """把复习词和新词组装成题目集合。"""
    pairs: list[tuple[Word, int | None]] = [(w, box) for w, box in review]
    pairs += [(w, None) for w in new]
    rng.shuffle(pairs)   # 打乱，避免"前面全是复习词"的固定节奏

    items = []
    for word, box in pairs:
        question = None
        if mode is PracticeMode.RECOGNITION:
            target = Candidate(word.id, word.meaning_primary, word.topic, word.part_of_speech)
            question = build_question(target, pool, rng)
        items.append(PracticeItem(word=word, box=box, question=question))

    return PracticeSet(
        mode=mode,
        items=tuple(items),
        review_count=len(review),
        new_count=len(new),
    )


async def build_daily_set(
    db: AsyncSession,
    user: User,
    mode: PracticeMode,
    word_list_id: uuid.UUID,
    now: datetime,
    rng: random.Random | None = None,
) -> PracticeSet:
    """每日任务 —— 到期复习词优先，再用新词补足。

    配额取用户级别的 `daily_review_limit` / `daily_new_limit`。
    """
    rng = rng or random.Random()

    review = await _due_words(db, user.id, mode, word_list_id, now, user.daily_review_limit)
    new = await _new_words(db, user.id, mode, word_list_id, user.daily_new_limit)

    pool = await _load_candidate_pool(db, word_list_id) if mode is PracticeMode.RECOGNITION else []
    return _assemble(mode, review, new, pool, rng)


async def build_free_set(
    db: AsyncSession,
    user: User,
    mode: PracticeMode,
    word_list_id: uuid.UUID,
    count: int,
    scope: str,
    topic: str | None,
    now: datetime,
    rng: random.Random | None = None,
) -> PracticeSet:
    """自由练习 —— 用户自选范围和数量。

    scope: all | review_only | new_only | topic
    """
    rng = rng or random.Random()

    review: list[tuple[Word, int]] = []
    new: list[Word] = []

    if scope == "review_only":
        review = await _due_words(db, user.id, mode, word_list_id, now, count)
    elif scope == "new_only":
        new = await _new_words(db, user.id, mode, word_list_id, count)
    elif scope == "topic":
        if not topic:
            raise ValueError("scope=topic 时必须指定 topic")
        words = list(
            (
                await db.execute(
                    select(Word)
                    .where(Word.word_list_id == word_list_id, Word.topic == topic)
                    .order_by(func.random())
                    .limit(count)
                )
            )
            .scalars()
            .all()
        )
        boxes = await _boxes_for(db, user.id, mode, [w.id for w in words])
        review = [(w, boxes[w.id]) for w in words if w.id in boxes]
        new = [w for w in words if w.id not in boxes]
    else:  # all
        review = await _due_words(db, user.id, mode, word_list_id, now, count)
        new = await _new_words(db, user.id, mode, word_list_id, count - len(review))

    pool = await _load_candidate_pool(db, word_list_id) if mode is PracticeMode.RECOGNITION else []
    return _assemble(mode, review, new, pool, rng)


async def _boxes_for(
    db: AsyncSession,
    user_id: uuid.UUID,
    mode: PracticeMode,
    word_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not word_ids:
        return {}
    rows = (
        await db.execute(
            select(UserProgress.word_id, UserProgress.box).where(
                UserProgress.user_id == user_id,
                UserProgress.mode == mode,
                UserProgress.word_id.in_(word_ids),
            )
        )
    ).all()
    return {wid: box for wid, box in rows}


# ─────────────────────────────────────────────────────────────
# 答题
# ─────────────────────────────────────────────────────────────


def _judge_recognition(user_input: str, word: Word) -> bool:
    """阅读模式判定 —— 比对用户选中的**文本**与正确释义。

    ⚠️ 为什么是文本而不是选项 index（与 docs/04 初稿不同）：

        题目是无状态生成的（每次请求现算），服务端不保存"第几个是对的"。
        如果客户端回传 index，服务端无从验证 —— 除非把题目存进 session
        或用种子重新生成，两者都更复杂且更脆弱。

        回传文本则完全无状态：客户端本来就有全部 4 个选项文本，
        但**不知道哪个对**，所以照样不能作弊。

        额外好处：事件日志里存文本比存 index 更有分析价值 ——
        index 脱离当次题目就毫无意义，文本能直接看出用户选了什么。

    `unknown`（点了"不知道"）恒判错，但事件里保留原值，
    将来能区分"主动承认不会"和"蒙错了"。
    """
    if not user_input or user_input == UNKNOWN_ANSWER:
        return False
    return user_input.strip() == strip_pos_prefix(word.meaning_primary)


async def submit_answer(
    db: AsyncSession,
    user: User,
    word: Word,
    mode: PracticeMode,
    user_input: str,
    answered_at: datetime,
    device_id: str | None,
    test_session_id: uuid.UUID | None = None,
) -> AnswerOutcome:
    """提交一次答题 —— 事件溯源的写路径。

    两步（ADR-002）：
        1. 追加一条 `answer_events`  ← 不可变，事实来源
        2. 更新 `user_progress`      ← 缓存

    ⚠️ **测试模式（`test_session_id` 非空）只做第 1 步。**
       测试"错了就是错了"，不进 Leitner 循环（ADR-013）。
       事件仍然记录 —— 要出成绩、要进错题本 —— 只是回放时会跳过。

    ⚠️ 第 2 步优先走**增量**（快）；但如果这条事件比已记录的
       `last_answered_at` 更早（离线补传），增量不等价于全量，
       必须回退到**全量回放**。这个判断是正确性的关键。
    """
    is_test = test_session_id is not None

    # ── 判定 ──
    dictation_result = None
    if mode is PracticeMode.DICTATION:
        dictation_result = judge_dictation(user_input, word.word)
        is_correct = dictation_result.is_correct
    else:
        is_correct = _judge_recognition(user_input, word)

    # ── 1. 追加事件（只 INSERT，永不改）──
    event = AnswerEvent(
        user_id=user.id,
        word_id=word.id,
        mode=mode,
        is_correct=is_correct,
        user_input=user_input,
        answered_at=answered_at,
        device_id=device_id,
        is_test=is_test,
        test_session_id=test_session_id,
    )
    db.add(event)
    await db.flush()          # 拿到自增 id，回放排序要用

    # ── 测试模式到此为止：不碰进度 ──
    if is_test:
        await db.commit()
        return AnswerOutcome(
            is_correct=is_correct,
            correct_answer=word.word if mode is PracticeMode.DICTATION else word.meaning_primary,
            dictation=dictation_result,
            progress=None,
            was_replayed=False,
            event_id=event.id,
        )

    # ── 2. 更新进度缓存 ──
    progress = (
        await db.execute(
            select(UserProgress).where(
                UserProgress.user_id == user.id,
                UserProgress.word_id == word.id,
                UserProgress.mode == mode,
            )
        )
    ).scalar_one_or_none()

    record = AnswerRecord(event.id, is_correct, answered_at)

    # 判断能否走增量：新事件必须不早于已记录的最后一次答题
    can_increment = (
        progress is None
        or progress.last_answered_at is None
        or answered_at >= progress.last_answered_at
    )

    if can_increment:
        current = (
            None
            if progress is None
            else ProgressSnapshot(
                box=progress.box,
                next_review_at=progress.next_review_at,
                correct_count=progress.correct_count,
                wrong_count=progress.wrong_count,
                last_answered_at=progress.last_answered_at,
            )
        )
        snapshot = replay_incremental(current, record)
        was_replayed = False
    else:
        # 乱序事件 —— 增量会算错，必须全量重放
        snapshot = await rebuild_progress(db, user.id, word.id, mode)
        assert snapshot is not None   # 刚插入了事件，至少有一条
        was_replayed = True

    if progress is None:
        progress = UserProgress(user_id=user.id, word_id=word.id, mode=mode)
        db.add(progress)

    progress.box = snapshot.box
    progress.next_review_at = snapshot.next_review_at
    progress.correct_count = snapshot.correct_count
    progress.wrong_count = snapshot.wrong_count
    progress.last_answered_at = snapshot.last_answered_at

    await db.commit()

    return AnswerOutcome(
        is_correct=is_correct,
        correct_answer=word.word if mode is PracticeMode.DICTATION else word.meaning_primary,
        dictation=dictation_result,
        progress=snapshot,
        was_replayed=was_replayed,
        event_id=event.id,
    )


async def rebuild_progress(
    db: AsyncSession,
    user_id: uuid.UUID,
    word_id: uuid.UUID,
    mode: PracticeMode,
) -> ProgressSnapshot | None:
    """从事件全量重建某个 (用户, 单词, 模式) 的进度。

    这是 ADR-002 那句"任何一行都能从事件重算出来"的具体兑现。

    ⚠️ **必须把 is_test / corrects_event_id 一起查出来**，否则 replay()
       无法跳过测试事件、无法应用更正 —— 会把更正事件当成又一次答对，
       把测试答错当成真实答错。（这个坑踩过，见 BUG-010）
    """
    rows = (
        await db.execute(
            select(
                AnswerEvent.id,
                AnswerEvent.is_correct,
                AnswerEvent.answered_at,
                AnswerEvent.is_test,
                AnswerEvent.corrects_event_id,
            ).where(
                AnswerEvent.user_id == user_id,
                AnswerEvent.word_id == word_id,
                AnswerEvent.mode == mode,
            )
        )
    ).all()
    return replay(
        [
            AnswerRecord(eid, ok, at, is_test=is_test, corrects_event_id=corrects)
            for eid, ok, at, is_test, corrects in rows
        ]
    )
