"""学习进度统计、错题本、从事件重建进度。

═══════════════════════════════════════════════════════════════════════
⚠️ 关于"今日"和"连续天数"的时区
═══════════════════════════════════════════════════════════════════════

服务器跑在 UTC（Docker 容器），用户在东八区。
按 UTC 切分"天"会出错：

    北京时间 2026-07-25 07:00  =  UTC 2026-07-24 23:00
    → 今天的学习被记成昨天，连续天数也会断错

所以由**客户端传时区偏移**（分钟，东为正）：

    JS:  -new Date().getTimezoneOffset()     // 北京 → 480

比在 users 表存时区更好：用户出差换时区时自动跟随，不需要改设置。

SQL 里的写法有个坑：`answered_at` 是 TIMESTAMPTZ，
直接 `(answered_at + interval)::date` 会按**会话时区**解释，行为不确定。
必须先 `AT TIME ZONE 'UTC'` 转成 naive 再算：

    (answered_at AT TIME ZONE 'UTC' + make_interval(mins => :offset))::date
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import Integer, bindparam, delete, func, select, text
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnswerEvent, PracticeMode, UserProgress, Word
from app.models.enums import BOX_INTERVALS
from app.services.replay import AnswerRecord, replay

__all__ = [
    "DailyStats",
    "ProgressSummary",
    "RebuildResult",
    "WrongWordEntry",
    "build_summary",
    "list_wrong_words",
    "rebuild_all_progress",
]

#: 错题本里每个词保留最近几次的错误输入
RECENT_INPUT_LIMIT = 3


@dataclass(frozen=True, slots=True)
class DailyStats:
    answered: int
    correct: int

    @property
    def accuracy(self) -> float:
        """正确率。没答题时返回 0，不是除零错误。"""
        return round(self.correct / self.answered, 4) if self.answered else 0.0


@dataclass(frozen=True, slots=True)
class ProgressSummary:
    streak_days: int
    today: DailyStats
    #: {模式: {盒子号: 词数}}。只统计有进度记录的词
    boxes: dict[PracticeMode, dict[int, int]]
    #: {模式: 今日到期词数}
    due_now: dict[PracticeMode, int]
    total_answered: int


@dataclass(frozen=True, slots=True)
class WrongWordEntry:
    word: Word
    mode: PracticeMode
    wrong_count: int
    last_wrong_at: datetime
    #: 最近几次答错时的输入 —— 能看出总是怎么拼错的
    recent_inputs: tuple[str, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class RebuildResult:
    rebuilt: int      # 重建的 (词, 模式) 组合数
    removed: int      # 删掉的孤儿进度行（有进度但无事件）
    events_read: int


# ─────────────────────────────────────────────────────────────
# 连续学习天数
# ─────────────────────────────────────────────────────────────


async def _active_days(
    db: AsyncSession, user_id: uuid.UUID, tz_offset_minutes: int
) -> list[date]:
    """用户有过答题的**本地日期**列表，倒序。

    distinct 后的行数受用户使用天数限制（几百天封顶），可以安全拉进内存。
    """
    stmt = (
        select(
            func.date(
                func.timezone("UTC", AnswerEvent.answered_at)
                + func.make_interval(0, 0, 0, 0, 0, bindparam("tz_mins", type_=Integer))
            ).label("local_day")
        )
        .where(AnswerEvent.user_id == user_id)
        .distinct()
        .order_by(text("local_day DESC"))
    )
    rows = (await db.execute(stmt, {"tz_mins": tz_offset_minutes})).scalars().all()
    return list(rows)


def compute_streak(active_days: list[date], today: date) -> int:
    """从活跃日期列表算连续天数。纯函数，方便单测。

    规则：**今天没学不算断** —— 从今天或昨天起往回数连续的日子。
    否则用户每天早上打开应用都会看到"连续 0 天"，体验很糟。

    >>> from datetime import date
    >>> t = date(2026, 7, 25)
    >>> compute_streak([date(2026,7,25), date(2026,7,24), date(2026,7,23)], t)
    3
    >>> compute_streak([date(2026,7,24), date(2026,7,23)], t)   # 今天还没学
    2
    >>> compute_streak([date(2026,7,23)], t)                    # 昨天断了
    0
    >>> compute_streak([], t)
    0
    """
    if not active_days:
        return 0

    days = sorted(set(active_days), reverse=True)
    latest = days[0]

    # 最近一次活动既不是今天也不是昨天 → 已经断了
    if latest < today - timedelta(days=1):
        return 0

    streak = 1
    for prev, cur in zip(days, days[1:], strict=False):
        if prev - cur == timedelta(days=1):
            streak += 1
        else:
            break
    return streak


# ─────────────────────────────────────────────────────────────
# 总览
# ─────────────────────────────────────────────────────────────


async def build_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    now: datetime,
    tz_offset_minutes: int = 0,
) -> ProgressSummary:
    """学习总览。"""
    local_today = (now + timedelta(minutes=tz_offset_minutes)).date()

    # ── 今日答题量 ──
    today_answered, today_correct = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(AnswerEvent.is_correct.is_(True)),
            )
            .select_from(AnswerEvent)
            .where(
                AnswerEvent.user_id == user_id,
                func.date(
                    func.timezone("UTC", AnswerEvent.answered_at)
                    + func.make_interval(
                        0, 0, 0, 0, 0, bindparam("tz_mins", type_=Integer)
                    )
                )
                == local_today,
            ),
            {"tz_mins": tz_offset_minutes},
        )
    ).one()

    total_answered = (
        await db.execute(
            select(func.count())
            .select_from(AnswerEvent)
            .where(AnswerEvent.user_id == user_id)
        )
    ).scalar_one()

    # ── 盒子分布 ──
    boxes: dict[PracticeMode, dict[int, int]] = {
        mode: dict.fromkeys(BOX_INTERVALS, 0) for mode in PracticeMode
    }
    for mode, box, count in (
        await db.execute(
            select(UserProgress.mode, UserProgress.box, func.count())
            .where(UserProgress.user_id == user_id)
            .group_by(UserProgress.mode, UserProgress.box)
        )
    ).all():
        boxes[mode][box] = count

    # ── 到期待复习 ──
    due_now: dict[PracticeMode, int] = dict.fromkeys(PracticeMode, 0)
    for mode, count in (
        await db.execute(
            select(UserProgress.mode, func.count())
            .where(
                UserProgress.user_id == user_id,
                UserProgress.next_review_at <= now,
            )
            .group_by(UserProgress.mode)
        )
    ).all():
        due_now[mode] = count

    return ProgressSummary(
        streak_days=compute_streak(
            await _active_days(db, user_id, tz_offset_minutes), local_today
        ),
        today=DailyStats(answered=today_answered, correct=today_correct),
        boxes=boxes,
        due_now=due_now,
        total_answered=total_answered,
    )


# ─────────────────────────────────────────────────────────────
# 错题本
# ─────────────────────────────────────────────────────────────


async def list_wrong_words(
    db: AsyncSession,
    user_id: uuid.UUID,
    mode: PracticeMode | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[WrongWordEntry], int]:
    """错题本 —— 直接从 `answer_events` 聚合。

    返回 (条目, 总数)。

    这是事件溯源"免费送"的能力：因为存的是每一次答题而非最终状态，
    才能回答"这个词错过几次""每次都怎么拼错的"。
    只存 `user_progress` 的话这些都做不到（见 learning-docs/05）。

    `recent_inputs` 用 PostgreSQL 的 `array_agg(... ORDER BY ...)` 一次拿到，
    不必为每个词再查一遍。
    """
    conditions = [AnswerEvent.user_id == user_id, AnswerEvent.is_correct.is_(False)]
    if mode is not None:
        conditions.append(AnswerEvent.mode == mode)

    grouped = (
        select(
            AnswerEvent.word_id,
            AnswerEvent.mode,
            func.count().label("wrong_count"),
            func.max(AnswerEvent.answered_at).label("last_wrong_at"),
            # 按答题时间排序聚合，取最近几次的输入
            func.array_agg(
                aggregate_order_by(AnswerEvent.user_input, AnswerEvent.answered_at.desc())
            ).label("inputs"),
        )
        .where(*conditions)
        .group_by(AnswerEvent.word_id, AnswerEvent.mode)
        .subquery()
    )

    total = (
        await db.execute(select(func.count()).select_from(grouped))
    ).scalar_one()

    rows = (
        await db.execute(
            select(Word, grouped.c.mode, grouped.c.wrong_count,
                   grouped.c.last_wrong_at, grouped.c.inputs)
            .join(grouped, grouped.c.word_id == Word.id)
            .order_by(grouped.c.wrong_count.desc(), grouped.c.last_wrong_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    entries = [
        WrongWordEntry(
            word=word,
            mode=row_mode,
            wrong_count=wrong_count,
            last_wrong_at=last_wrong_at,
            # array_agg 已按 answered_at DESC 聚合 → 最近的在前
            recent_inputs=tuple(i for i in (inputs or []) if i)[:RECENT_INPUT_LIMIT],
        )
        for word, row_mode, wrong_count, last_wrong_at, inputs in rows
    ]
    return entries, total


# ─────────────────────────────────────────────────────────────
# 从事件重建进度
# ─────────────────────────────────────────────────────────────


async def rebuild_all_progress(
    db: AsyncSession,
    user_id: uuid.UUID,
    word_id: uuid.UUID | None = None,
) -> RebuildResult:
    """把 `user_progress` 整个从 `answer_events` 重算出来。

    ⭐ 这个函数是 ADR-002 那句承诺的兑现：

        user_progress 的任何一行，都能从 answer_events 完整重算出来。

    用途：
        - 多端冲突后手动修复
        - 改了 Leitner 参数，需要按新规则重建全部历史
        - 怀疑进度表脏了

    顺带清理**孤儿进度行**（有进度记录但一条事件都没有）——
    那种行不可能由事件产生，只能是脏数据。
    """
    conditions = [AnswerEvent.user_id == user_id]
    if word_id is not None:
        conditions.append(AnswerEvent.word_id == word_id)

    events = (
        await db.execute(
            select(
                AnswerEvent.id,
                AnswerEvent.word_id,
                AnswerEvent.mode,
                AnswerEvent.is_correct,
                AnswerEvent.answered_at,
            ).where(*conditions)
        )
    ).all()

    # 按 (词, 模式) 分组 —— 进度的主键就是这个三元组（加 user_id）
    grouped: dict[tuple[uuid.UUID, PracticeMode], list[AnswerRecord]] = defaultdict(list)
    for event_id, wid, mode, is_correct, answered_at in events:
        grouped[(wid, mode)].append(AnswerRecord(event_id, is_correct, answered_at))

    # 现有的进度行（用于找孤儿）
    existing_conditions = [UserProgress.user_id == user_id]
    if word_id is not None:
        existing_conditions.append(UserProgress.word_id == word_id)

    existing = {
        (p.word_id, p.mode): p
        for p in (
            await db.execute(select(UserProgress).where(*existing_conditions))
        ).scalars()
    }

    rebuilt = 0
    for (wid, mode), records in grouped.items():
        snapshot = replay(records)
        if snapshot is None:      # 分组非空，理论上不会发生
            continue

        row = existing.pop((wid, mode), None)
        if row is None:
            row = UserProgress(user_id=user_id, word_id=wid, mode=mode)
            db.add(row)

        row.box = snapshot.box
        row.next_review_at = snapshot.next_review_at
        row.correct_count = snapshot.correct_count
        row.wrong_count = snapshot.wrong_count
        row.last_answered_at = snapshot.last_answered_at
        rebuilt += 1

    # existing 里剩下的都是孤儿：有进度行但没有对应事件
    removed = 0
    for (wid, mode) in list(existing):
        await db.execute(
            delete(UserProgress).where(
                UserProgress.user_id == user_id,
                UserProgress.word_id == wid,
                UserProgress.mode == mode,
            )
        )
        removed += 1

    await db.commit()
    return RebuildResult(rebuilt=rebuilt, removed=removed, events_read=len(events))
