"""听写判定与 diff 对齐的单测。

判定规则来自 docs/01-product-spec.md §2.1：严格匹配，但标出错处。
测试用例大量取自**真实的雅思拼写错误模式**（漏双写字母、ie/ei 颠倒等）。
"""

import pytest

from app.services.dictation import (
    CharStatus,
    DictationResult,
    judge_dictation,
    normalize_input,
)


def errors(result: DictationResult) -> list[tuple[str, str, str | None]]:
    """把非 OK 的位置提出来，方便断言。"""
    return [
        (d.status.value, d.char, d.expected)
        for d in result.diff
        if d.status is not CharStatus.OK
    ]


# ─────────────────────────────────────────────────────────────
# 归一化
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "expected"), [
    ("  accommodate  ", "accommodate"),
    ("\taccommodate\n", "accommodate"),
    ("accommodate", "accommodate"),
    ("", ""),
    ("   ", ""),
    (None, ""),
])
def test_normalize_input(raw, expected):
    assert normalize_input(raw) == expected


def test_normalize_keeps_inner_space():
    """中间空格**不能**去掉 —— "ice berg" vs "iceberg" 是拼写错误，不是格式问题。"""
    assert normalize_input(" ice berg ") == "ice berg"


def test_normalize_keeps_case():
    """归一化不改大小写 —— 大小写在**比较时**忽略，但展示要保留用户输入。"""
    assert normalize_input(" Accommodate ") == "Accommodate"


# ─────────────────────────────────────────────────────────────
# 判对
# ─────────────────────────────────────────────────────────────

def test_exact_match_is_correct():
    r = judge_dictation("accommodate", "accommodate")
    assert r.is_correct
    assert r.error_count == 0
    assert all(d.status is CharStatus.OK for d in r.diff)
    assert len(r.diff) == len("accommodate")


@pytest.mark.parametrize("user", [
    "  accommodate  ",
    "\taccommodate",
    "accommodate\n",
])
def test_surrounding_whitespace_ignored(user):
    assert judge_dictation(user, "accommodate").is_correct


@pytest.mark.parametrize("user", [
    "Accommodate",
    "ACCOMMODATE",
    "aCCoMMoDaTe",
    "  AcCoMmOdAtE  ",
])
def test_case_ignored(user):
    assert judge_dictation(user, "accommodate").is_correct


def test_correct_diff_shows_standard_spelling():
    """判对时 diff 展示**标准拼写**，不是用户的大小写。

    这样前端渲染出来总是规范形式。
    """
    r = judge_dictation("ACCOMMODATE", "accommodate")
    assert "".join(d.char for d in r.diff) == "accommodate"


# ─────────────────────────────────────────────────────────────
# 判错 —— 严格，不容错
# ─────────────────────────────────────────────────────────────

def test_one_letter_off_is_wrong():
    """差一个字母就是错 —— 规则明确不容错。"""
    assert judge_dictation("accomodate", "accommodate").is_correct is False


def test_inner_space_is_wrong():
    assert judge_dictation("accom modate", "accommodate").is_correct is False


def test_empty_input_is_wrong():
    r = judge_dictation("", "accommodate")
    assert r.is_correct is False
    assert r.error_count == len("accommodate")
    assert all(d.status is CharStatus.MISSING for d in r.diff)


def test_none_input_is_wrong():
    r = judge_dictation(None, "accommodate")
    assert r.is_correct is False
    assert all(d.status is CharStatus.MISSING for d in r.diff)


# ─────────────────────────────────────────────────────────────
# ⭐ 对齐质量 —— 真实雅思拼写错误模式
# ─────────────────────────────────────────────────────────────

def test_missing_double_letter_reports_one_error():
    """⭐ 漏一个双写字母，只该报 1 处错。

    这是最能体现"为什么需要编辑距离对齐"的例子：
    逐位比较会让位置 5 之后全部错位，报 6 个错，用户看不出问题在哪。
    """
    r = judge_dictation("accomodate", "accommodate")
    assert errors(r) == [("missing", "", "m")]
    assert r.error_count == 1


def test_missing_letter_in_middle():
    r = judge_dictation("enviroment", "environment")
    assert errors(r) == [("missing", "", "n")]


def test_missing_first_of_double_c():
    r = judge_dictation("acommodate", "accommodate")
    assert errors(r) == [("missing", "", "c")]


def test_substituted_letter():
    r = judge_dictation("accommadate", "accommodate")
    assert errors(r) == [("wrong", "a", "o")]


def test_extra_letter_at_end():
    r = judge_dictation("accommodatee", "accommodate")
    assert errors(r) == [("extra", "e", None)]


def test_extra_letter_in_middle():
    r = judge_dictation("acccommodate", "accommodate")
    assert errors(r) == [("extra", "c", None)]


def test_transposed_letters_ie_ei():
    """ie/ei 颠倒 —— 最小编辑是 2 次替换。"""
    r = judge_dictation("recieve", "receive")
    assert r.error_count == 2
    assert all(s == "wrong" for s, _, _ in errors(r))


def test_completely_different_word():
    r = judge_dictation("xyz", "abc")
    assert errors(r) == [("wrong", "x", "a"), ("wrong", "y", "b"), ("wrong", "z", "c")]


def test_multiple_error_types_together():
    """一次输入里同时有漏、多、错。"""
    r = judge_dictation("acomodatte", "accommodate")
    assert r.is_correct is False
    kinds = {s for s, _, _ in errors(r)}
    assert len(kinds) >= 2, f"应有多种错误类型，实际 {kinds}"


# ─────────────────────────────────────────────────────────────
# diff 结构不变式
# ─────────────────────────────────────────────────────────────

def test_diff_positions_are_sequential_from_zero():
    """pos 必须是从 0 开始的连续整数 —— 前端按它渲染。"""
    r = judge_dictation("acomodatte", "accommodate")
    assert [d.pos for d in r.diff] == list(range(len(r.diff)))


def test_missing_char_is_empty_string_not_none():
    """MISSING 时 char 是空串而非 None —— 前端不用做空值判断。"""
    r = judge_dictation("accomodate", "accommodate")
    missing = [d for d in r.diff if d.status is CharStatus.MISSING]
    assert missing
    assert all(d.char == "" for d in missing)


def test_expected_present_only_for_wrong_and_missing():
    """expected 只在 WRONG / MISSING 时有值。"""
    r = judge_dictation("acomodatte", "accommodate")
    for d in r.diff:
        if d.status in (CharStatus.WRONG, CharStatus.MISSING):
            assert d.expected, f"{d.status} 应该有 expected：{d}"
        else:
            assert d.expected is None, f"{d.status} 不该有 expected：{d}"


def test_reconstructing_correct_answer_from_diff():
    """⭐ 从 diff 能还原出正确答案 —— 证明对齐没丢字符。

    规则：OK 和 WRONG 位置取 expected/char，MISSING 取 expected，EXTRA 跳过。
    """
    for user, correct in [
        ("accomodate", "accommodate"),
        ("acccommodate", "accommodate"),
        ("recieve", "receive"),
        ("xyz", "abc"),
        ("", "environment"),
        ("acomodatte", "accommodate"),
    ]:
        r = judge_dictation(user, correct)
        rebuilt = "".join(
            (d.expected or d.char) if d.status is not CharStatus.EXTRA else ""
            for d in r.diff
        )
        assert rebuilt.lower() == correct.lower(), f"{user!r} vs {correct!r} → {rebuilt!r}"


def test_reconstructing_user_input_from_diff():
    """⭐ 从 diff 也能还原出用户输入 —— 双向都不丢信息。"""
    for user, correct in [
        ("accomodate", "accommodate"),
        ("acccommodate", "accommodate"),
        ("acomodatte", "accommodate"),
    ]:
        r = judge_dictation(user, correct)
        rebuilt = "".join(d.char for d in r.diff)
        assert rebuilt == user


# ─────────────────────────────────────────────────────────────
# 纯函数性质
# ─────────────────────────────────────────────────────────────

def test_judge_is_deterministic():
    """同样输入永远同样输出 —— 包括 diff 的 tie-break 顺序。"""
    results = [judge_dictation("acomodatte", "accommodate") for _ in range(30)]
    assert len(set(results)) == 1


def test_result_is_immutable():
    r = judge_dictation("accomodate", "accommodate")
    with pytest.raises((AttributeError, TypeError)):
        r.is_correct = True  # type: ignore[misc]


def test_diff_char_is_immutable():
    r = judge_dictation("accomodate", "accommodate")
    with pytest.raises((AttributeError, TypeError)):
        r.diff[0].char = "z"  # type: ignore[misc]


def test_empty_correct_answer_rejected():
    """正确答案为空说明数据有问题，早失败。"""
    with pytest.raises(ValueError, match="correct_answer"):
        judge_dictation("anything", "")


# ─────────────────────────────────────────────────────────────
# 真实词库里的词
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("user", "correct", "ok"), [
    ("accommodation", "accommodation", True),
    ("accomodation", "accommodation", False),
    ("necessary", "necessary", True),
    ("neccessary", "necessary", False),
    ("occurrence", "occurrence", True),
    ("occurence", "occurrence", False),
    ("separate", "separate", True),
    ("seperate", "separate", False),
    ("definitely", "definitely", True),
    ("definately", "definitely", False),
])
def test_common_ielts_misspellings(user, correct, ok):
    assert judge_dictation(user, correct).is_correct is ok


def test_phrase_with_space():
    """词库里有短语（account for / bring about），空格是拼写的一部分。"""
    assert judge_dictation("account for", "account for").is_correct
    assert judge_dictation("accountfor", "account for").is_correct is False
