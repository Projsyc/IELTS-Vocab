"""听写判定与错误位置高亮 —— 纯函数。

═══════════════════════════════════════════════════════════════════════
判定规则（ADR: docs/01-product-spec.md §2.1）
═══════════════════════════════════════════════════════════════════════

**严格匹配，但标出错处**：

    忽略   首尾空格、大小写
    不容错  差一个字母即判错
    判错时  高亮显示错误位置

═══════════════════════════════════════════════════════════════════════
为什么需要编辑距离对齐
═══════════════════════════════════════════════════════════════════════

逐位比较在**漏字母**时会全线崩掉：

    用户   a c c o m o d a t e        （少了一个 m）
    正确   a c c o m m o d a t e

    逐位比较：位置 5 之后全部错位 → 报 6 个错，用户看不出问题在哪
    对齐后：  只报"位置 5 少了个 m" → 一眼看懂

所以用 Levenshtein 动态规划求**最小编辑脚本**再回溯出对齐。
单词只有十几个字符，DP 矩阵很小，性能不是问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "CharStatus",
    "DiffChar",
    "DictationResult",
    "judge_dictation",
    "normalize_input",
]


class CharStatus(str, Enum):
    """对齐后每个位置的状态。

    继承 str 以便 Pydantic 直接序列化成字符串。
    """

    OK = "ok"            # 拼对了
    WRONG = "wrong"      # 拼错了（该位置字母不对）
    MISSING = "missing"  # 漏了字母
    EXTRA = "extra"      # 多打了字母


@dataclass(frozen=True, slots=True)
class DiffChar:
    """对齐结果里的一个位置。

    Attributes:
        pos:      在展示序列里的下标（从 0 开始，连续）
        char:     用户打的字符。MISSING 时为空串
        status:   该位置的状态
        expected: 正确字符。WRONG / MISSING 时有值，OK / EXTRA 时为 None
    """

    pos: int
    char: str
    status: CharStatus
    expected: str | None = None


@dataclass(frozen=True, slots=True)
class DictationResult:
    """一次听写判定的完整结果。"""

    is_correct: bool
    correct_answer: str
    user_input: str
    diff: tuple[DiffChar, ...]

    @property
    def error_count(self) -> int:
        """错误位置数量（OK 之外的都算）。"""
        return sum(1 for d in self.diff if d.status is not CharStatus.OK)


def normalize_input(text: str | None) -> str:
    """判定前的归一化 —— 只做规则允许的两件事。

    ⚠️ 刻意**不做**的事：
        不去除中间空格（"ice berg" vs "iceberg" 应判错，那是拼写问题）
        不去除标点、不做任何模糊化（规则是"严格匹配"）

    >>> normalize_input("  Accommodate  ")
    'Accommodate'
    >>> normalize_input(None)
    ''
    """
    if not text:
        return ""
    return text.strip()


def _align(user: str, correct: str) -> list[tuple[str, str, str]]:
    """Levenshtein 对齐，返回 [(操作, 用户字符, 正确字符), ...]。

    操作取值：match / sub / missing / extra
    大小写不敏感（比较时转小写，但返回原字符用于展示）。
    """
    m, n = len(user), len(correct)

    # dp[i][j] = 把 user[:i] 变成 correct[:j] 的最小编辑数
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        dp[i][0] = i          # 用户多打了 i 个字符
    for j in range(1, n + 1):
        dp[0][j] = j          # 用户漏了 j 个字符

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if user[i - 1].lower() == correct[j - 1].lower():
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(
                    dp[i - 1][j - 1],   # 替换
                    dp[i][j - 1],       # 漏字母
                    dp[i - 1][j],       # 多字母
                )

    # 回溯。tie-break 顺序固定（对角 → 漏 → 多），保证输出确定。
    ops: list[tuple[str, str, str]] = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and user[i - 1].lower() == correct[j - 1].lower():
            ops.append(("match", user[i - 1], correct[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append(("sub", user[i - 1], correct[j - 1]))
            i, j = i - 1, j - 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            ops.append(("missing", "", correct[j - 1]))
            j -= 1
        else:
            ops.append(("extra", user[i - 1], ""))
            i -= 1

    ops.reverse()
    return ops


def judge_dictation(user_input: str | None, correct_answer: str) -> DictationResult:
    """判定一次听写，并生成用于高亮的逐字符对齐。

    Args:
        user_input:     用户拼写的内容（可能为 None / 空串）
        correct_answer: 正确单词

    Returns:
        DictationResult，含 is_correct 与 diff。

    >>> r = judge_dictation("accomodate", "accommodate")   # 少一个 m
    >>> r.is_correct
    False
    >>> [(d.status.value, d.char, d.expected) for d in r.diff if d.status.value != 'ok']
    [('missing', '', 'm')]

    >>> judge_dictation("  Accommodate ", "accommodate").is_correct   # 空格+大小写
    True
    """
    if not correct_answer:
        raise ValueError("correct_answer 不能为空")

    user = normalize_input(user_input)
    correct = correct_answer.strip()

    # 判定：忽略首尾空格与大小写后必须完全一致
    is_correct = user.lower() == correct.lower()

    if is_correct:
        # 全对时不必跑 DP，直接按正确答案生成 —— 也保证展示的是标准拼写
        diff = tuple(
            DiffChar(pos=i, char=ch, status=CharStatus.OK)
            for i, ch in enumerate(correct)
        )
        return DictationResult(
            is_correct=True,
            correct_answer=correct,
            user_input=user,
            diff=diff,
        )

    status_map = {
        "match": CharStatus.OK,
        "sub": CharStatus.WRONG,
        "missing": CharStatus.MISSING,
        "extra": CharStatus.EXTRA,
    }

    diff_chars: list[DiffChar] = []
    for pos, (op, user_ch, correct_ch) in enumerate(_align(user, correct)):
        status = status_map[op]
        diff_chars.append(
            DiffChar(
                pos=pos,
                char=user_ch,
                status=status,
                expected=correct_ch if status in (CharStatus.WRONG, CharStatus.MISSING) else None,
            )
        )

    return DictationResult(
        is_correct=False,
        correct_answer=correct,
        user_input=user,
        diff=tuple(diff_chars),
    )
