"""Leitner 算法单测。

这个算法会被回放成千上万次，且是 user_progress 可重算的前提，
所以测试覆盖得比较死 —— 包括那些"看起来显然"的性质。
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import BOX_INTERVALS, MAX_BOX, MIN_BOX
from app.services.leitner import (
    LeitnerState,
    apply_answer,
    initial_box,
    interval_days,
    is_due,
    is_mastered,
    next_review_after,
)

T0 = datetime(2026, 7, 25, 10, 30, tzinfo=UTC)


# ─────────────────────────────────────────────────────────────
# 常量一致性
# ─────────────────────────────────────────────────────────────

def test_box_intervals_match_adr():
    """间隔必须是 ADR-004 定的 1/2/4/7/15 天。

    改这些值等于改算法 —— 改完必须重放全部事件重建进度，
    所以这个测试挂了不是"改一下期望值"就完事。
    """
    assert BOX_INTERVALS == {1: 1, 2: 2, 3: 4, 4: 7, 5: 15}


def test_box_range_is_1_to_5():
    assert MIN_BOX == 1
    assert MAX_BOX == 5
    assert set(BOX_INTERVALS) == {1, 2, 3, 4, 5}


def test_intervals_are_strictly_increasing():
    """间隔必须单调递增 —— 否则"升箱"就不代表"延长复习间隔"了。"""
    values = [BOX_INTERVALS[b] for b in sorted(BOX_INTERVALS)]
    assert values == sorted(values)
    assert len(set(values)) == len(values), "间隔不该有重复"


def test_initial_box_is_min():
    assert initial_box() == MIN_BOX


# ─────────────────────────────────────────────────────────────
# 答对 → 升一箱
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("box", "expected"), [(1, 2), (2, 3), (3, 4), (4, 5)])
def test_correct_promotes_one_box(box, expected):
    assert apply_answer(box, True, T0).box == expected


def test_correct_at_max_box_stays():
    """Box 5 封顶，不会变成 6。"""
    assert apply_answer(MAX_BOX, True, T0).box == MAX_BOX


# ─────────────────────────────────────────────────────────────
# 答错 → 回 Box 1（不是降一箱）
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("box", [1, 2, 3, 4, 5])
def test_wrong_resets_to_box_one(box):
    """答错**直接回 Box 1**，这是 ADR-004 刻意的选择。

    如果哪天改成"降一箱"，这个测试会挂 —— 那时记得同步改 ADR 和文档。
    """
    assert apply_answer(box, False, T0).box == MIN_BOX


def test_wrong_is_not_decrement():
    """明确区分"回第一箱"和"降一箱"：Box 4 答错该到 1，不是 3。"""
    assert apply_answer(4, False, T0).box == 1
    assert apply_answer(4, False, T0).box != 3


# ─────────────────────────────────────────────────────────────
# 下次复习时刻
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("box", "days"), sorted(BOX_INTERVALS.items()))
def test_next_review_uses_new_box_interval(box, days):
    assert next_review_after(box, T0) == T0 + timedelta(days=days)


def test_next_review_based_on_answered_at_not_now():
    """基准必须是 answered_at。

    如果实现里用了 datetime.now()，这个测试会挂 —— 因为期望值
    是从传入的时刻算的，与"现在"无关。
    """
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    state = apply_answer(1, True, long_ago)
    assert state.next_review_at == long_ago + timedelta(days=BOX_INTERVALS[2])
    assert state.next_review_at.year == 2020


def test_correct_answer_extends_interval():
    """答对后复习间隔应该变长（这是间隔重复的意义）。"""
    before = next_review_after(2, T0) - T0
    after = apply_answer(2, True, T0).next_review_at - T0
    assert after > before


def test_wrong_answer_shortens_interval():
    """答错后间隔应缩到最短（1 天）。"""
    state = apply_answer(5, False, T0)
    assert state.next_review_at - T0 == timedelta(days=1)


# ─────────────────────────────────────────────────────────────
# 纯函数性质
# ─────────────────────────────────────────────────────────────

def test_apply_answer_is_deterministic():
    """同样输入永远同样输出 —— 事件回放能工作的根本前提。"""
    results = [apply_answer(3, True, T0) for _ in range(50)]
    assert len(set(results)) == 1, "同样的输入产生了不同的输出，纯函数性质被破坏"


def test_state_is_immutable():
    """frozen dataclass：调用方不可能意外改掉历史状态。"""
    state = apply_answer(2, True, T0)
    with pytest.raises((AttributeError, TypeError)):
        state.box = 99  # type: ignore[misc]


def test_apply_answer_does_not_mutate_inputs():
    """入参不该被修改（datetime 本身不可变，但把意图写清楚）。"""
    at = datetime(2026, 7, 25, tzinfo=UTC)
    snapshot = at
    apply_answer(1, True, at)
    assert at == snapshot


# ─────────────────────────────────────────────────────────────
# 输入校验 —— 早失败好过晚出错
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_box", [0, 6, -1, 100])
def test_apply_answer_rejects_invalid_box(bad_box):
    with pytest.raises(ValueError, match="box"):
        apply_answer(bad_box, True, T0)


def test_apply_answer_rejects_naive_datetime():
    """数据库列是 TIMESTAMPTZ，naive datetime 会被静默按本地时区解释。"""
    naive = datetime(2026, 7, 25, 10, 30)   # 没有 tzinfo
    with pytest.raises(ValueError, match="时区"):
        apply_answer(1, True, naive)


def test_state_rejects_naive_datetime():
    with pytest.raises(ValueError, match="时区"):
        LeitnerState(box=1, next_review_at=datetime(2026, 7, 25))


@pytest.mark.parametrize("bad_box", [0, 6, -1])
def test_state_rejects_out_of_range_box(bad_box):
    with pytest.raises(ValueError, match="box"):
        LeitnerState(box=bad_box, next_review_at=T0)


def test_interval_days_rejects_invalid_box():
    with pytest.raises(ValueError, match="box"):
        interval_days(0)


# ─────────────────────────────────────────────────────────────
# 到期判断
# ─────────────────────────────────────────────────────────────

def test_is_due_at_exact_moment():
    """恰好到点算到期（用 <= 而非 <）—— 否则边界上的词会被漏掉一轮。"""
    state = LeitnerState(box=2, next_review_at=T0)
    assert is_due(state, T0) is True


def test_is_due_before_and_after():
    state = LeitnerState(box=2, next_review_at=T0)
    assert is_due(state, T0 - timedelta(seconds=1)) is False
    assert is_due(state, T0 + timedelta(seconds=1)) is True


def test_is_due_rejects_naive_now():
    state = LeitnerState(box=2, next_review_at=T0)
    with pytest.raises(ValueError, match="时区"):
        is_due(state, datetime(2026, 7, 26))


@pytest.mark.parametrize(("box", "expected"), [(1, False), (4, False), (5, True)])
def test_is_mastered(box, expected):
    assert is_mastered(box) is expected


# ─────────────────────────────────────────────────────────────
# 端到端场景：文档里那个例子
# ─────────────────────────────────────────────────────────────

def test_accommodate_scenario_from_docs():
    """learning-docs/04 里 accommodate 的例子，逐步验证。

    7/1 新词答对    1 → 2   下次 7/3
    7/3 答对        2 → 3   下次 7/7
    7/7 答错(少个m) 3 → 1   下次 7/8
    7/8 答对        1 → 2   下次 7/10
    """
    d = lambda day: datetime(2026, 7, day, tzinfo=UTC)  # noqa: E731

    s = apply_answer(initial_box(), True, d(1))
    assert (s.box, s.next_review_at) == (2, d(3))

    s = apply_answer(s.box, True, d(3))
    assert (s.box, s.next_review_at) == (3, d(7))

    s = apply_answer(s.box, False, d(7))
    assert (s.box, s.next_review_at) == (1, d(8))

    s = apply_answer(s.box, True, d(8))
    assert (s.box, s.next_review_at) == (2, d(10))


def test_five_consecutive_correct_reaches_mastery():
    """从新词连对 4 次进 Box 5。"""
    box = initial_box()
    for i in range(4):
        box = apply_answer(box, True, T0 + timedelta(days=i)).box
    assert box == MAX_BOX
    assert is_mastered(box)


def test_one_mistake_undoes_all_progress():
    """Box 5 答错一次回到 Box 1 —— ADR-004 承认这很严厉，是刻意的。"""
    box = initial_box()
    for i in range(4):
        box = apply_answer(box, True, T0 + timedelta(days=i)).box
    assert box == MAX_BOX
    assert apply_answer(box, False, T0 + timedelta(days=20)).box == MIN_BOX
