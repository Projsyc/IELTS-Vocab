"""事件回放单测。

═══════════════════════════════════════════════════════════════════════
这个文件守护的是混合式事件溯源（ADR-002）的核心承诺：

    user_progress 的任何一行，都能从 answer_events 完整重算出来。

最重要的两条性质：

    1. 乱序回放 == 顺序回放      ← 多端同步的正确性全靠这条
    2. 时间戳相同时结果确定      ← 否则回放不可复现
═══════════════════════════════════════════════════════════════════════
"""

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.services.leitner import MAX_BOX, MIN_BOX
from app.services.replay import (
    AnswerRecord,
    ProgressSnapshot,
    replay,
    replay_incremental,
    sort_events,
)

T0 = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def rec(event_id: int, correct: bool, offset_hours: float) -> AnswerRecord:
    """构造一条事件，时刻是 T0 + offset 小时。"""
    return AnswerRecord(
        event_id=event_id,
        is_correct=correct,
        answered_at=T0 + timedelta(hours=offset_hours),
    )


# ─────────────────────────────────────────────────────────────
# ⭐ 性质 1：乱序回放 == 顺序回放
# ─────────────────────────────────────────────────────────────

def test_shuffled_replay_equals_ordered_replay():
    """⭐ 最重要的一条测试。

    多端同步时事件会乱序到达（离线补传）。只要事件集合相同，
    回放结果就必须相同 —— 否则"从事件重算"这个承诺是假的。
    """
    events = [
        rec(1, True, 0),
        rec(2, True, 48),
        rec(3, False, 120),
        rec(4, True, 144),
        rec(5, True, 192),
        rec(6, False, 300),
    ]
    expected = replay(events)

    rng = random.Random(12345)
    for _ in range(200):
        shuffled = events[:]
        rng.shuffle(shuffled)
        assert replay(shuffled) == expected, "乱序回放得到了不同结果"


def test_shuffled_replay_on_random_sequences():
    """在多组随机生成的事件序列上验证同一条性质。

    固定种子保证可复现 —— 挂了能重跑出同样的反例。
    """
    rng = random.Random(999)

    for trial in range(60):
        n = rng.randint(1, 25)
        events = [
            AnswerRecord(
                event_id=i + 1,
                is_correct=rng.random() < 0.65,
                answered_at=T0 + timedelta(hours=rng.randint(0, 2000)),
            )
            for i in range(n)
        ]
        expected = replay(events)

        for _ in range(8):
            shuffled = events[:]
            rng.shuffle(shuffled)
            assert replay(shuffled) == expected, f"第 {trial} 组序列乱序后结果不一致"


def test_replay_sorts_by_answered_at_not_event_id():
    """⚠️ 必须按 answered_at 排，不能按 id。

    真实场景：
        手机 10:00 答对（离线）  → 15:05 才上传，拿到 id=102
        电脑 14:00 答错          → 当场入库，id=101

    按 id 排 → 错(14:00) 再 对(10:00) → Box 2   ❌
    按时间排 → 对(10:00) 再 错(14:00) → Box 1   ✅
    """
    phone_correct = AnswerRecord(102, True, T0 + timedelta(hours=1))
    laptop_wrong = AnswerRecord(101, False, T0 + timedelta(hours=5))

    snap = replay([phone_correct, laptop_wrong])
    assert snap is not None
    assert snap.box == MIN_BOX, "最后一个动作是答错，应该在 Box 1"
    assert snap.last_answered_at == laptop_wrong.answered_at

    # 反过来构造：如果实现按 id 排，结果会是 Box 2
    assert snap.box != 2, "看起来是按 event_id 排序了，不是按 answered_at"


# ─────────────────────────────────────────────────────────────
# ⭐ 性质 2：时间戳相同时结果确定
# ─────────────────────────────────────────────────────────────

def test_identical_timestamps_are_ordered_by_event_id():
    """时间戳完全相同时，用 event_id 做次级键。

    为什么必须有次级键：顺序不同结果不同 ——
        先对后错 → Box 1
        先错后对 → Box 2
    没有确定的次级键，回放就不可复现。
    """
    same_time = T0 + timedelta(hours=3)
    correct_first = AnswerRecord(10, True, same_time)
    wrong_second = AnswerRecord(11, False, same_time)

    snap = replay([wrong_second, correct_first])   # 刻意反着传
    assert snap is not None
    assert snap.box == MIN_BOX, "id=10(对) 应先于 id=11(错)，最终在 Box 1"


def test_identical_timestamps_reverse_id_order():
    """把 id 顺序反过来，结果也该跟着反 —— 证明 id 真的参与了排序。"""
    same_time = T0 + timedelta(hours=3)
    wrong_first = AnswerRecord(10, False, same_time)
    correct_second = AnswerRecord(11, True, same_time)

    snap = replay([correct_second, wrong_first])
    assert snap is not None
    assert snap.box == 2, "id=10(错) 先，id=11(对) 后 → Box 1 升到 2"


def test_all_identical_timestamps_is_deterministic():
    """一批事件时间戳全相同时，多次乱序回放结果必须一致。"""
    same_time = T0
    events = [
        AnswerRecord(i, i % 3 != 0, same_time)
        for i in range(1, 13)
    ]
    expected = replay(events)

    rng = random.Random(7)
    for _ in range(100):
        shuffled = events[:]
        rng.shuffle(shuffled)
        assert replay(shuffled) == expected


def test_sort_events_key_order():
    t = T0 + timedelta(hours=2)
    a = AnswerRecord(102, True, t)
    b = AnswerRecord(101, False, t)
    c = AnswerRecord(50, True, t - timedelta(hours=1))

    assert [e.event_id for e in sort_events([a, b, c])] == [50, 101, 102]


# ─────────────────────────────────────────────────────────────
# 基本行为
# ─────────────────────────────────────────────────────────────

def test_empty_events_returns_none():
    """没有事件 ⇒ 该词是新词 ⇒ user_progress 里不该有行。"""
    assert replay([]) is None


def test_single_correct_event():
    snap = replay([rec(1, True, 0)])
    assert snap is not None
    assert snap.box == 2
    assert snap.correct_count == 1
    assert snap.wrong_count == 0
    assert snap.last_answered_at == T0


def test_single_wrong_event():
    snap = replay([rec(1, False, 0)])
    assert snap is not None
    assert snap.box == MIN_BOX
    assert snap.correct_count == 0
    assert snap.wrong_count == 1


def test_counts_match_events():
    events = [
        rec(1, True, 0), rec(2, False, 24), rec(3, True, 48),
        rec(4, True, 72), rec(5, False, 96),
    ]
    snap = replay(events)
    assert snap is not None
    assert snap.correct_count == 3
    assert snap.wrong_count == 2
    assert snap.total_count == 5


def test_last_answered_at_is_latest_not_last_passed():
    """last_answered_at 取时间最晚的，不是传入列表的最后一个。"""
    events = [rec(1, True, 100), rec(2, True, 5), rec(3, True, 50)]
    snap = replay(events)
    assert snap is not None
    assert snap.last_answered_at == T0 + timedelta(hours=100)


def test_next_review_derives_from_last_event():
    """下次复习时刻基于**最后一个事件**的答题时刻，不是第一个。"""
    events = [rec(1, True, 0), rec(2, True, 48)]
    snap = replay(events)
    assert snap is not None
    last_at = T0 + timedelta(hours=48)
    assert snap.next_review_at == last_at + timedelta(days=4)   # Box 3 → 4 天


def test_five_consecutive_correct_reaches_mastery():
    events = [rec(i + 1, True, i * 24) for i in range(6)]
    snap = replay(events)
    assert snap is not None
    assert snap.box == MAX_BOX


def test_wrong_at_end_resets_regardless_of_history():
    """不管前面对了多少次，最后答错就回 Box 1。"""
    events = [rec(i + 1, True, i * 24) for i in range(10)]
    events.append(rec(99, False, 500))
    snap = replay(events)
    assert snap is not None
    assert snap.box == MIN_BOX


# ─────────────────────────────────────────────────────────────
# 输入校验
# ─────────────────────────────────────────────────────────────

def test_record_rejects_naive_datetime():
    with pytest.raises(ValueError, match="时区"):
        AnswerRecord(1, True, datetime(2026, 7, 1, 9, 0))


def test_replay_accepts_generator():
    """接受任意可迭代对象，不只是 list。"""
    events = (rec(i + 1, True, i * 24) for i in range(3))
    snap = replay(events)
    assert snap is not None
    assert snap.correct_count == 3


def test_replay_does_not_mutate_input_list():
    """排序不该改动调用方的列表。"""
    events = [rec(3, True, 100), rec(1, True, 0), rec(2, False, 50)]
    original = events[:]
    replay(events)
    assert events == original, "replay 修改了传入的列表"


# ─────────────────────────────────────────────────────────────
# ⭐ 性质 3：增量更新 == 全量回放
# ─────────────────────────────────────────────────────────────

def test_incremental_equals_full_replay_when_appending_in_order():
    """⭐ 增量更新是性能优化，全量回放是真相 —— 两者必须一致。

    答题接口走增量（快），冲突修复走全量。如果这两条路会得到不同结果，
    数据就会悄悄不一致。
    """
    events = [
        rec(1, True, 0), rec(2, True, 48), rec(3, False, 120),
        rec(4, True, 144), rec(5, True, 200), rec(6, True, 260),
        rec(7, False, 400), rec(8, True, 500),
    ]

    incremental: ProgressSnapshot | None = None
    for i, event in enumerate(events, 1):
        incremental = replay_incremental(incremental, event)
        full = replay(events[:i])
        assert incremental == full, f"第 {i} 个事件后增量与全量不一致"


def test_incremental_equals_full_on_random_sequences():
    """随机序列上验证增量与全量的等价性。"""
    rng = random.Random(4242)

    for trial in range(40):
        n = rng.randint(1, 20)
        events = []
        t = T0
        for i in range(n):
            t += timedelta(hours=rng.randint(1, 100))   # 严格递增，保证是"最新事件"
            events.append(AnswerRecord(i + 1, rng.random() < 0.6, t))

        incremental: ProgressSnapshot | None = None
        for i, event in enumerate(events, 1):
            incremental = replay_incremental(incremental, event)
            assert incremental == replay(events[:i]), f"第 {trial} 组第 {i} 步不一致"


def test_incremental_from_none_matches_single_event_replay():
    event = rec(1, True, 0)
    assert replay_incremental(None, event) == replay([event])


def test_incremental_is_wrong_for_out_of_order_events():
    """明确记录增量更新的**已知局限**。

    增量只在"新事件确实是最新的"时候等价于全量。
    离线补传的事件可能更早 —— 那时调用方必须走全量回放。
    这个测试固化这个边界，防止有人误以为增量总是安全的。
    """
    early = rec(1, True, 0)
    late = rec(2, False, 100)

    # 先处理晚的，再增量塞一个更早的 —— 结果会与全量不同
    snap = replay_incremental(None, late)
    snap = replay_incremental(snap, early)

    full = replay([early, late])
    assert snap != full, (
        "乱序时增量竟然与全量一致 —— 要么实现变了，"
        "要么这个局限已消除，两种情况都该更新文档"
    )
    # 全量才是对的：最后一个动作（100h 处）是答错 → Box 1
    assert full is not None and full.box == MIN_BOX


# ─────────────────────────────────────────────────────────────
# 快照不可变
# ─────────────────────────────────────────────────────────────

def test_snapshot_is_immutable():
    snap = replay([rec(1, True, 0)])
    assert snap is not None
    with pytest.raises((AttributeError, TypeError)):
        snap.box = 99  # type: ignore[misc]
