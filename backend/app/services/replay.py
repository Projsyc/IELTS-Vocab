"""事件回放 —— 从 answer_events 重建 user_progress。

═══════════════════════════════════════════════════════════════════════
这个模块存在的意义
═══════════════════════════════════════════════════════════════════════

混合式事件溯源（ADR-002）的核心承诺：

    user_progress 的任何一行，都能从 answer_events 完整重算出来。

本模块就是那个"重算"。它让下面这些事变得可能：

    多端冲突     离线补传的事件乱序到达 → 按真实答题顺序重放，结果确定
    算法变更     Leitner 参数改了 → 重放全部历史，进度自动重建
    数据修复     怀疑 progress 脏了 → POST /api/progress/rebuild

═══════════════════════════════════════════════════════════════════════
⚠️ 排序是正确性的关键
═══════════════════════════════════════════════════════════════════════

**必须按 answered_at（客户端答题时刻）排序，不能按 created_at 或自增 id。**

离线答的题可能几小时后才上传，入库顺序 ≠ 真实发生顺序：

    手机 10:00 答对（离线）              入库时刻 15:05  id=102
    电脑 14:00 答错                      入库时刻 14:00  id=101
    手机 15:05 联网补传上面那条

    按 id 排:          错(14:00) → 对(10:00)  →  Box 2   ❌ 顺序颠倒
    按 answered_at 排: 对(10:00) → 错(14:00)  →  Box 1   ✅ 正确

**时间戳相同时必须有确定的次级排序键。**

顺序不同结果就不同 —— 先对后错进 Box 1，先错后对进 Box 2。
所以用事件的自增 `id` 做次级键：它在数据库里唯一且稳定，
保证同一批事件每次回放都得到同样的结果。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.services.leitner import LeitnerState, apply_answer, initial_box

__all__ = [
    "AnswerRecord",
    "ProgressSnapshot",
    "replay",
    "replay_incremental",
    "sort_events",
]



@dataclass(frozen=True, slots=True)
class AnswerRecord:
    """回放所需的最小事件信息。

    刻意**不依赖 ORM 模型** —— 这样单测不需要数据库，
    而且将来事件表加字段也不会影响回放逻辑。

    Attributes:
        event_id:    事件自增主键。时间戳相同时作为确定性次级排序键。
        is_correct:  答对没。
        answered_at: 客户端答题时刻（必须 aware）。回放的主排序键。
    """

    event_id: int
    is_correct: bool
    answered_at: datetime

    def __post_init__(self) -> None:
        if self.answered_at.tzinfo is None:
            raise ValueError("answered_at 必须带时区信息（aware datetime）")


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """回放结果 —— 可直接写回 user_progress 表。

    ⚠️ 这里的每个字段都必须能从事件算出来。
       想加"事件里没有的信息"（比如用户备注）时，说明设计要破了，
       停下来重新想 —— 见 ADR-002 的铁律。
    """

    box: int
    next_review_at: datetime
    correct_count: int
    wrong_count: int
    last_answered_at: datetime

    @property
    def total_count(self) -> int:
        return self.correct_count + self.wrong_count


def sort_events(events: Iterable[AnswerRecord]) -> list[AnswerRecord]:
    """按 (answered_at, event_id) 排序。

    单独抽出来是为了能独立测试排序规则本身 —— 它是回放正确性的前提。

    >>> from datetime import UTC, datetime
    >>> t = datetime(2026, 7, 25, 10, tzinfo=UTC)
    >>> a = AnswerRecord(102, True, t)
    >>> b = AnswerRecord(101, False, t)          # 同一时刻，id 更小
    >>> [e.event_id for e in sort_events([a, b])]
    [101, 102]
    """
    return sorted(events, key=lambda e: (e.answered_at, e.event_id))


def replay(events: Sequence[AnswerRecord] | Iterable[AnswerRecord]) -> ProgressSnapshot | None:
    """从零回放全部事件，重建某个 (用户, 单词, 模式) 的进度。

    返回 None 表示没有任何事件 —— 该词对该用户是"新词"，
    在 user_progress 里就不该有行。

    纯函数：同样的事件集合（无论传入顺序）永远得到同样的结果。

    >>> from datetime import UTC, datetime, timedelta
    >>> t0 = datetime(2026, 7, 1, tzinfo=UTC)
    >>> evs = [
    ...     AnswerRecord(1, True,  t0),                      # Box 1 → 2
    ...     AnswerRecord(2, True,  t0 + timedelta(days=2)),  # Box 2 → 3
    ...     AnswerRecord(3, False, t0 + timedelta(days=6)),  # 答错 → Box 1
    ... ]
    >>> snap = replay(evs)
    >>> snap.box, snap.correct_count, snap.wrong_count
    (1, 2, 1)
    """
    ordered = sort_events(events)
    if not ordered:
        return None

    box = initial_box()
    state: LeitnerState | None = None
    correct = 0
    wrong = 0

    for event in ordered:
        state = apply_answer(box, event.is_correct, event.answered_at)
        box = state.box
        if event.is_correct:
            correct += 1
        else:
            wrong += 1

    # ordered 非空 ⇒ 循环至少执行一次 ⇒ state 一定不是 None
    assert state is not None

    return ProgressSnapshot(
        box=state.box,
        next_review_at=state.next_review_at,
        correct_count=correct,
        wrong_count=wrong,
        last_answered_at=ordered[-1].answered_at,
    )


def replay_incremental(
    current: ProgressSnapshot | None,
    new_event: AnswerRecord,
) -> ProgressSnapshot:
    """增量更新 —— 用户答一道题时走这条路（快），不必重放全部历史。

    ⚠️ **只有在 new_event 确实是最新事件时才等价于全量回放。**
       离线补传的事件可能比已记录的更早，那时必须走 `replay()` 全量重算。
       调用方负责判断：`new_event.answered_at >= current.last_answered_at`。

    这个函数是性能优化，`replay()` 才是真相。两者结果必须一致 ——
    已有测试（test_replay.py）在随机事件序列上验证这个等价性。
    """
    if current is None:
        state = apply_answer(initial_box(), new_event.is_correct, new_event.answered_at)
        return ProgressSnapshot(
            box=state.box,
            next_review_at=state.next_review_at,
            correct_count=1 if new_event.is_correct else 0,
            wrong_count=0 if new_event.is_correct else 1,
            last_answered_at=new_event.answered_at,
        )

    state = apply_answer(current.box, new_event.is_correct, new_event.answered_at)
    return ProgressSnapshot(
        box=state.box,
        next_review_at=state.next_review_at,
        correct_count=current.correct_count + (1 if new_event.is_correct else 0),
        wrong_count=current.wrong_count + (0 if new_event.is_correct else 1),
        last_answered_at=max(current.last_answered_at, new_event.answered_at),
    )
