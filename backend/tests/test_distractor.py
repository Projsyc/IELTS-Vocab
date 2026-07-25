"""干扰项生成的单测。

守护两件事：
  1. 降级链按预期工作
  2. ⭐ **词性前缀不泄露答案** —— 这是 2026-07-25 功能测试实测到的问题
"""

import random
import uuid

import pytest

from app.services.distractor import (
    DISTRACTOR_COUNT,
    OPTION_COUNT,
    Candidate,
    FallbackLevel,
    build_question,
    strip_pos_prefix,
)

RNG = lambda seed=42: random.Random(seed)  # noqa: E731


def cand(n: int, meaning: str, topic: str | None = None, pos: str | None = None) -> Candidate:
    return Candidate(uuid.UUID(int=n), meaning, topic, pos)


def make_pool(count: int, topic: str | None, pos: str | None, start: int = 1) -> list[Candidate]:
    prefix = f"{pos} " if pos else ""
    return [
        cand(i, f"{prefix}释义{i}", topic, pos)
        for i in range(start, start + count)
    ]


# ─────────────────────────────────────────────────────────────
# 词性前缀剥离
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "expected"), [
    ("n. 交集, 十字路口, 交叉点", "交集, 十字路口, 交叉点"),
    ("vt. 废止, 革除, 消灭", "废止, 革除, 消灭"),
    ("a. 抽象的, 深奥的", "抽象的, 深奥的"),
    ("adv. 突然地", "突然地"),
    ("abbr. 略语", "略语"),
    ("num. 三", "三"),
    ("  n.  带空格", "带空格"),
])
def test_strip_pos_prefix(raw, expected):
    assert strip_pos_prefix(raw) == expected


@pytest.mark.parametrize("raw", [
    "[经] 能力, 才能",       # 领域标记不是词性，要保留
    "[计] 摘录; 摘要",
    "容纳",                  # 本来就没前缀
    "3. 第三",               # 数字开头不是词性
])
def test_strip_keeps_non_pos_prefixes(raw):
    assert strip_pos_prefix(raw) == raw.strip()


def test_strip_only_removes_first_prefix():
    """只剥开头一个，不递归剥。"""
    assert strip_pos_prefix("n. a. 双前缀") == "a. 双前缀"


def test_strip_handles_empty():
    assert strip_pos_prefix("") == ""


# ─────────────────────────────────────────────────────────────
# ⭐ 核心：词性前缀不泄露答案
# ─────────────────────────────────────────────────────────────

def test_options_never_contain_pos_prefix():
    """⭐ 所有选项都不带词性前缀。

    这是防泄露的兜底 —— 即便降级到混词性也不会露馅。
    """
    target = cand(0, "n. 探索, 寻求", "教育", "n.")
    pool = [
        cand(1, "a. 学院的, 学术的", "教育", "a."),
        cand(2, "vt. 学习, 认识到", "教育", "vt."),
        cand(3, "vt. 减去, 扣掉", "教育", "vt."),
    ]
    q = build_question(target, pool, RNG())

    for opt in q.options:
        assert not opt.text.startswith(("n.", "v.", "a.", "vt.", "vi.", "adv.")), (
            f"选项带了词性前缀，会泄露答案：{opt.text!r}"
        )


def test_mixed_pos_options_are_indistinguishable_by_prefix():
    """⭐ 真实回归场景：quest 那道题。

    降级到"同话题混词性"时，正确答案是唯一的 n.，
    如果保留前缀用户数一下就知道选哪个。
    """
    target = cand(0, "n. 探索, 寻求", "教育", "n.")
    pool = [
        cand(1, "a. 学院的, 学术的", "教育", "a."),
        cand(2, "vt. 学习, 认识到", "教育", "vt."),
        cand(3, "vt. 减去, 扣掉", "教育", "vt."),
    ]
    q = build_question(target, pool, RNG())

    # 没有任何一个选项能靠"格式与众不同"被挑出来
    import re
    prefixed = [o for o in q.options if re.match(r"^[a-z]+\.", o.text)]
    assert not prefixed, f"这些选项仍带前缀：{[o.text for o in prefixed]}"


# ─────────────────────────────────────────────────────────────
# 降级链
# ─────────────────────────────────────────────────────────────

def test_prefers_same_topic_and_pos():
    target = cand(0, "n. 目标词", "科技", "n.")
    pool = (
        make_pool(5, "科技", "n.", start=1)        # 同话题同词性，够用
        + make_pool(5, "科技", "vt.", start=10)
        + make_pool(5, "环境", "n.", start=20)
    )
    q = build_question(target, pool, RNG())
    assert q.fallback_level is FallbackLevel.TOPIC_AND_POS


def test_falls_back_to_topic_only():
    """同话题同词性不足 3 个 → 放宽词性。"""
    target = cand(0, "n. 目标词", "科技", "n.")
    pool = (
        make_pool(2, "科技", "n.", start=1)         # 只有 2 个，不够
        + make_pool(5, "科技", "vt.", start=10)     # 同话题别的词性
    )
    q = build_question(target, pool, RNG())
    assert q.fallback_level is FallbackLevel.TOPIC_ONLY
    assert q.is_complete


def test_falls_back_to_pos_only():
    """同话题整体不足 → 放宽话题，保住词性。"""
    target = cand(0, "n. 目标词", "全球化", "n.")
    pool = (
        make_pool(2, "全球化", "n.", start=1)       # 同话题只有 2 个
        + make_pool(6, "科技", "n.", start=10)      # 别的话题，同词性
    )
    q = build_question(target, pool, RNG())
    assert q.fallback_level is FallbackLevel.POS_ONLY
    assert q.is_complete


def test_falls_back_to_random():
    """话题和词性都凑不够 → 全库随机。"""
    target = cand(0, "n. 目标词", "全球化", "num.")
    pool = make_pool(6, "科技", "vt.", start=1)
    q = build_question(target, pool, RNG())
    assert q.fallback_level is FallbackLevel.RANDOM
    assert q.is_complete


def test_insufficient_pool_reports_honestly():
    """候选池太小时如实报告，不假装凑够了。"""
    target = cand(0, "n. 目标词", "科技", "n.")
    q = build_question(target, [cand(1, "n. 另一个", "科技", "n.")], RNG())
    assert q.fallback_level is FallbackLevel.INSUFFICIENT
    assert q.is_complete is False
    assert len(q.options) == 2


def test_empty_pool():
    target = cand(0, "n. 目标词", "科技", "n.")
    q = build_question(target, [], RNG())
    assert q.fallback_level is FallbackLevel.INSUFFICIENT
    assert len(q.options) == 1
    assert q.options[0].text == "目标词"


def test_target_with_no_topic_skips_to_pos():
    """未打标的词（v2 用户上传的）直接走词性降级。"""
    target = cand(0, "n. 目标词", None, "n.")
    pool = make_pool(6, "科技", "n.", start=1)
    q = build_question(target, pool, RNG())
    assert q.fallback_level is FallbackLevel.POS_ONLY


def test_target_with_no_topic_and_no_pos_goes_random():
    target = cand(0, "目标词", None, None)
    pool = make_pool(6, "科技", "n.", start=1)
    q = build_question(target, pool, RNG())
    assert q.fallback_level is FallbackLevel.RANDOM


# ─────────────────────────────────────────────────────────────
# 结构不变式
# ─────────────────────────────────────────────────────────────

def test_question_has_four_unique_options():
    target = cand(0, "n. 目标词", "科技", "n.")
    q = build_question(target, make_pool(20, "科技", "n.", start=1), RNG())
    assert len(q.options) == OPTION_COUNT
    assert len({o.text for o in q.options}) == OPTION_COUNT, "选项文本有重复"


def test_option_indices_are_one_to_four():
    """index 对应键盘 1/2/3/4，必须是 1..4 顺序。"""
    target = cand(0, "n. 目标词", "科技", "n.")
    q = build_question(target, make_pool(20, "科技", "n.", start=1), RNG())
    assert [o.index for o in q.options] == [1, 2, 3, 4]


def test_correct_index_points_to_target_meaning():
    target = cand(0, "n. 探索, 寻求", "科技", "n.")
    q = build_question(target, make_pool(20, "科技", "n.", start=1), RNG())
    assert q.options[q.correct_index - 1].text == "探索, 寻求"


def test_target_excluded_from_distractors():
    """目标词在候选池里时不能被当成自己的干扰项。"""
    target = cand(7, "n. 目标词", "科技", "n.")
    pool = make_pool(20, "科技", "n.", start=1)   # 含 id=7
    q = build_question(target, pool, RNG())
    assert sum(1 for o in q.options if o.text == "目标词") == 1


def test_duplicate_meanings_excluded():
    """⭐ 释义文本相同的候选要排除 —— 否则用户看到两个一样的选项，题就废了。"""
    target = cand(0, "n. 容纳", "科技", "n.")
    pool = [
        cand(1, "n. 容纳", "科技", "n."),        # 与正确答案撞车
        cand(2, "vt. 容纳", "科技", "n."),       # 剥前缀后也撞车
        cand(3, "n. 不同释义A", "科技", "n."),
        cand(4, "n. 不同释义B", "科技", "n."),
        cand(5, "n. 不同释义C", "科技", "n."),
    ]
    q = build_question(target, pool, RNG())
    texts = [o.text for o in q.options]
    assert len(texts) == len(set(texts)), f"出现重复选项：{texts}"
    assert texts.count("容纳") == 1


def test_duplicate_among_distractors_excluded():
    target = cand(0, "n. 目标", "科技", "n.")
    pool = [
        cand(1, "n. 一样的", "科技", "n."),
        cand(2, "n. 一样的", "科技", "n."),      # 与上一个撞车
        cand(3, "n. 不同A", "科技", "n."),
        cand(4, "n. 不同B", "科技", "n."),
    ]
    q = build_question(target, pool, RNG())
    texts = [o.text for o in q.options]
    assert len(texts) == len(set(texts))


def test_empty_meaning_rejected():
    with pytest.raises(ValueError, match="meaning_primary"):
        build_question(cand(0, ""), make_pool(5, None, None), RNG())


def test_candidates_with_empty_meaning_skipped():
    target = cand(0, "n. 目标", "科技", "n.")
    pool = [
        cand(1, "", "科技", "n."),
        cand(2, "n. ", "科技", "n."),            # 剥完是空
        cand(3, "n. 有效A", "科技", "n."),
        cand(4, "n. 有效B", "科技", "n."),
        cand(5, "n. 有效C", "科技", "n."),
    ]
    q = build_question(target, pool, RNG())
    assert all(o.text for o in q.options), "出现了空选项"
    assert q.is_complete


# ─────────────────────────────────────────────────────────────
# 随机性
# ─────────────────────────────────────────────────────────────

def test_same_seed_gives_same_question():
    """同种子结果可复现 —— 测试能稳定断言。"""
    target = cand(0, "n. 目标词", "科技", "n.")
    pool = make_pool(20, "科技", "n.", start=1)
    a = build_question(target, pool, RNG(7))
    b = build_question(target, pool, RNG(7))
    assert a == b


def test_different_seeds_shuffle_differently():
    """不同种子应产生不同排列（否则正确答案位置固定，用户会摸出规律）。"""
    target = cand(0, "n. 目标词", "科技", "n.")
    pool = make_pool(20, "科技", "n.", start=1)
    indices = {build_question(target, pool, RNG(s)).correct_index for s in range(30)}
    assert len(indices) > 1, "正确答案位置从不变化"


def test_correct_answer_position_is_well_distributed():
    """⭐ 正确答案应均匀落在 4 个位置。

    如果实现里忘了 shuffle，正确答案会永远在第 1 位 —— 用户几轮就摸出来了。
    """
    target = cand(0, "n. 目标词", "科技", "n.")
    pool = make_pool(20, "科技", "n.", start=1)
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for seed in range(400):
        counts[build_question(target, pool, RNG(seed)).correct_index] += 1

    assert all(c > 50 for c in counts.values()), f"分布不均：{counts}"


def test_question_is_immutable():
    target = cand(0, "n. 目标词", "科技", "n.")
    q = build_question(target, make_pool(20, "科技", "n.", start=1), RNG())
    with pytest.raises((AttributeError, TypeError)):
        q.correct_index = 1  # type: ignore[misc]


def test_pool_not_mutated():
    """不该改动调用方传入的候选池。"""
    target = cand(0, "n. 目标词", "科技", "n.")
    pool = make_pool(20, "科技", "n.", start=1)
    snapshot = pool[:]
    build_question(target, pool, RNG())
    assert pool == snapshot
