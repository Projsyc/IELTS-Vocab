"""话题体系与标签校验的单测。

重点是**校验逻辑**：LLM 经常不照白名单输出（"教育类"、"Education"、
"科技/互联网"），这些必须被拒掉，否则数据库里会出现几十个只用一次的
杂牌话题，干扰项分组就散了。
"""

import pytest

from app.scripts.topics import (
    TOPIC_GENERAL,
    TOPIC_HINTS,
    TOPIC_SET,
    TOPICS,
    is_valid_topic,
    normalize_topic,
    topic_list_for_prompt,
)


# ─────────────────────────────────────────────────────────────
# 话题体系本身的一致性
# ─────────────────────────────────────────────────────────────

def test_topics_are_unique():
    assert len(TOPICS) == len(set(TOPICS)), "话题列表有重复"


def test_every_topic_has_a_hint():
    """每个话题都必须有边界说明 —— 不加说明时 LLM 误标率明显上升。"""
    missing = [t for t in TOPICS if t not in TOPIC_HINTS]
    assert not missing, f"这些话题缺 TOPIC_HINTS 说明：{missing}"


def test_no_extra_hints():
    """TOPIC_HINTS 里不该有 TOPICS 之外的键（改名时容易漏删）。"""
    extra = set(TOPIC_HINTS) - set(TOPICS)
    assert not extra, f"TOPIC_HINTS 有多余的键：{extra}"


def test_general_topic_is_in_list():
    """兜底话题必须在白名单里，否则话题中立的词无处可去。"""
    assert TOPIC_GENERAL in TOPIC_SET


def test_topic_count_is_reasonable():
    """话题数量要够细（干扰项才有区分度）又不能太碎（每类词数不足）。

    4,768 词 ÷ 21 类 ≈ 227 词/类，抽 3 个干扰项绰绰有余。
    """
    assert 10 <= len(TOPICS) <= 30, f"话题数 {len(TOPICS)} 超出合理区间"


def test_topic_names_have_no_whitespace_issues():
    """话题名要直接写进数据库 varchar(50)，不能带首尾空格或超长。"""
    for t in TOPICS:
        assert t == t.strip(), f"话题名有首尾空格：{t!r}"
        assert len(t) <= 50, f"话题名超过 varchar(50)：{t!r}"
        assert "\n" not in t and "\t" not in t, f"话题名含换行/制表符：{t!r}"


def test_prompt_lists_all_topics():
    """给 LLM 的清单必须包含全部话题，漏一个就永远不会被标到。"""
    text = topic_list_for_prompt()
    for t in TOPICS:
        assert t in text, f"prompt 清单里缺 {t}"


# ─────────────────────────────────────────────────────────────
# is_valid_topic
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("topic", list(TOPICS))
def test_is_valid_accepts_all_whitelisted(topic):
    assert is_valid_topic(topic) is True


@pytest.mark.parametrize("topic", [
    "教育类",          # LLM 爱加后缀
    "Education",       # 翻译成英文
    "科技/互联网",      # 自己拼接
    "其他",
    "未知",
    "",
    None,
    "  教育  ",        # 带空格的原样输入应被拒（normalize 才负责清理）
])
def test_is_valid_rejects_non_whitelisted(topic):
    assert is_valid_topic(topic) is False


# ─────────────────────────────────────────────────────────────
# normalize_topic —— 保守清理
# ─────────────────────────────────────────────────────────────

def test_normalize_passes_through_valid():
    for t in TOPICS:
        assert normalize_topic(t) == t


@pytest.mark.parametrize(("raw", "expected"), [
    ("  教育  ", "教育"),
    ("\n科技\n", "科技"),
    ('"环境"', "环境"),
    ("'艺术'", "艺术"),
    ("「科技」", "科技"),
    ("【教育】", "教育"),
    ("(旅游)", "旅游"),
    ("《语言》", "语言"),
])
def test_normalize_strips_wrappers(raw, expected):
    assert normalize_topic(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("教育类", "教育"),
    ("环境话题", "环境"),
    ("科技领域", "科技"),
    ("交通方面", "交通"),
    ("艺术相关", "艺术"),
])
def test_normalize_strips_llm_suffixes(raw, expected):
    assert normalize_topic(raw) == expected


@pytest.mark.parametrize("raw", [
    "Education",            # 英文
    "科技/互联网",           # 拼接
    "教育与科研",            # 自创组合
    "其他",
    "N/A",
    "unknown",
    "",
    "   ",
    None,
    "「」",                  # 只有包裹符
])
def test_normalize_returns_none_for_unrecognizable(raw):
    """认不出就返回 None，**不做模糊猜测**。

    猜错了比留空更糟 —— 留空至少能通过 `topic IS NULL` 被下一轮重跑捡起来，
    猜错则会静默产出错误的干扰项分组。
    """
    assert normalize_topic(raw) is None


def test_normalize_does_not_fuzzy_match():
    """明确记录这个设计选择：不做相似度匹配。

    "教育与科研" 看着像"教育"，但强行对齐可能把本该归"科技"的词标错。
    """
    assert normalize_topic("教育与科研") is None
    assert normalize_topic("健康") is None          # 白名单是"健康与医疗"


def test_normalize_general_topic_with_slash():
    """兜底话题名含 `/`，别被包裹符清理逻辑误伤。"""
    assert normalize_topic(TOPIC_GENERAL) == TOPIC_GENERAL
    assert normalize_topic(f" {TOPIC_GENERAL} ") == TOPIC_GENERAL
