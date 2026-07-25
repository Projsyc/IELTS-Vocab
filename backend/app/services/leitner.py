"""Leitner 盒子算法 —— 纯函数实现。

═══════════════════════════════════════════════════════════════════════
为什么必须是纯函数
═══════════════════════════════════════════════════════════════════════

混合式事件溯源（ADR-002）要求：`user_progress` 的任何一行都能从
`answer_events` 完整重算出来。

这意味着状态转移必须满足：**同样的输入永远得到同样的输出**。
一旦引入 `datetime.now()`、随机数、数据库查询或任何外部状态，
重放同一批事件就可能得到不同结果，"可重算"这个性质就没了。

所以本模块：
  - 不读当前时间（时间由调用方从事件里传进来）
  - 不读数据库
  - 不修改任何入参
  - 无随机性

═══════════════════════════════════════════════════════════════════════
规则（ADR-004）
═══════════════════════════════════════════════════════════════════════

    答对 → 升一箱（Box 5 封顶）
    答错 → **直接掉回 Box 1**（不是降一箱）

    Box 1 → 1 天后复习    Box 4 → 7 天
    Box 2 → 2 天          Box 5 → 15 天（视为已掌握）
    Box 3 → 4 天
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.enums import BOX_INTERVALS, MAX_BOX, MIN_BOX

__all__ = [
    "LeitnerState",
    "apply_answer",
    "initial_box",
    "interval_days",
    "is_due",
    "is_mastered",
    "next_review_after",
]


@dataclass(frozen=True, slots=True)
class LeitnerState:
    """一个 (用户, 单词, 模式) 三元组的 Leitner 状态。

    frozen=True：不可变。转移函数返回新实例而不是改旧的 ——
    这样调用方不可能意外把历史状态改掉。
    """

    box: int
    next_review_at: datetime

    def __post_init__(self) -> None:
        if not MIN_BOX <= self.box <= MAX_BOX:
            raise ValueError(f"box 必须在 {MIN_BOX}–{MAX_BOX} 之间，得到 {self.box}")
        if self.next_review_at.tzinfo is None:
            # 数据库列是 TIMESTAMPTZ。naive datetime 混进来会导致比较时报错，
            # 或被静默按本地时区解释 —— 早失败好过晚出错。
            raise ValueError("next_review_at 必须带时区信息（aware datetime）")


def initial_box() -> int:
    """新词的起始盒子号。"""
    return MIN_BOX


def interval_days(box: int) -> int:
    """某个盒子的复习间隔（天）。

    >>> interval_days(1)
    1
    >>> interval_days(5)
    15
    """
    if box not in BOX_INTERVALS:
        raise ValueError(f"未知的 box: {box}（合法值 {sorted(BOX_INTERVALS)}）")
    return BOX_INTERVALS[box]


def next_review_after(box: int, answered_at: datetime) -> datetime:
    """给定盒子号和答题时刻，算出下次复习时刻。

    ⚠️ 基准是 **answered_at**（答题时刻），不是"现在"。

       离线答的题可能几小时后才上传，用"现在"做基准会把复习日推迟；
       更糟的是会让回放结果依赖于**回放的时机** —— 同一批事件今天
       重算和明天重算得到不同结果，纯函数性质就没了。
    """
    if answered_at.tzinfo is None:
        raise ValueError("answered_at 必须带时区信息（aware datetime）")
    return answered_at + timedelta(days=interval_days(box))


def apply_answer(box: int, is_correct: bool, answered_at: datetime) -> LeitnerState:
    """核心状态转移。

    这是整个算法的全部内容 —— 刻意保持到只有几行，
    因为它要被回放成千上万次，必须简单到不会出错。

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 7, 25, tzinfo=UTC)
    >>> apply_answer(2, True, at).box        # 答对升一箱
    3
    >>> apply_answer(4, False, at).box       # 答错直接回 Box 1，不是降到 3
    1
    >>> apply_answer(5, True, at).box        # Box 5 封顶
    5
    """
    if box not in BOX_INTERVALS:
        raise ValueError(f"未知的 box: {box}（合法值 {sorted(BOX_INTERVALS)}）")

    if is_correct:
        new_box = min(box + 1, MAX_BOX)
    else:
        new_box = MIN_BOX          # 答错回第一箱，不是 box - 1

    return LeitnerState(
        box=new_box,
        next_review_at=next_review_after(new_box, answered_at),
    )


def is_due(state: LeitnerState, now: datetime) -> bool:
    """这个词现在该复习了吗？

    `now` 由调用方传入而非内部取 —— 保持纯函数，也方便测试。

    >>> from datetime import UTC, datetime
    >>> s = LeitnerState(2, datetime(2026, 7, 25, tzinfo=UTC))
    >>> is_due(s, datetime(2026, 7, 26, tzinfo=UTC))
    True
    >>> is_due(s, datetime(2026, 7, 24, tzinfo=UTC))
    False
    """
    if now.tzinfo is None:
        raise ValueError("now 必须带时区信息（aware datetime）")
    return state.next_review_at <= now


def is_mastered(box: int) -> bool:
    """是否已掌握（进了顶格盒子）。

    >>> is_mastered(5)
    True
    >>> is_mastered(4)
    False
    """
    return box >= MAX_BOX
