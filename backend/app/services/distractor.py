"""阅读模式的干扰项生成 —— 纯函数。

═══════════════════════════════════════════════════════════════════════
为什么干扰项要挑
═══════════════════════════════════════════════════════════════════════

全库随机会出现 `apple` 配「量子力学」这种选项，4 选 1 毫无区分度。
所以从**同雅思话题**内抽（ADR-003 / ADR-012）。

═══════════════════════════════════════════════════════════════════════
⚠️ 词性前缀会泄露答案
═══════════════════════════════════════════════════════════════════════

释义自带词性前缀（`n. 交集, 十字路口` / `vt. 减去, 扣掉`）。
2026-07-25 打标后做功能测试时实测到：

    quest (正确答案 n. 探索, 寻求)
      1. a. 学院的, 学术的      ← 词性不同
      2. vt. 学习；认识到        ← 词性不同
      3. vt. 减去, 扣掉          ← 词性不同
      4. n. 探索, 寻求           ← 唯一的 n.，不看意思就能选对

**两层防护**：

    1. 优先从**同词性**里抽干扰项（也让语义更接近，一举两得）
    2. **展示时一律剥掉词性前缀** —— 即便降级到混词性也不会泄露

第 2 条是兜底。既然总要剥，索性一直剥：选项更短好读，
词性在单词旁边显示一次就够了，重复四遍反而是噪音。

═══════════════════════════════════════════════════════════════════════
降级链
═══════════════════════════════════════════════════════════════════════

    同话题 + 同词性     ← 首选，语义最接近
      ↓ 不足 3 个
    同话题（放宽词性）
      ↓ 不足 3 个
    同词性（放宽话题）
      ↓ 不足 3 个
    全库随机            ← 最后兜底

4,768 词 ÷ 21 话题 ÷ 约 10 种词性 → 部分组合必然偏薄，降级链一定会用到。
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Candidate",
    "FallbackLevel",
    "Option",
    "Question",
    "build_question",
    "strip_pos_prefix",
]

#: 选项总数（1 个正确 + 3 个干扰）
OPTION_COUNT = 4
#: 需要的干扰项数
DISTRACTOR_COUNT = OPTION_COUNT - 1

#: 词性前缀，如 "n. " / "vt. " / "adj. "。
#: 只匹配小写字母加点的组合 —— 不能误伤 `[经]` `[计]` 这类领域标记。
_POS_PREFIX_RE = re.compile(r"^\s*(?:[a-z]+\.)+\s*")


class FallbackLevel(str, Enum):
    """实际用了降级链的哪一级。用于诊断干扰项质量。"""

    TOPIC_AND_POS = "topic_and_pos"   # 同话题 + 同词性
    TOPIC_ONLY = "topic_only"         # 同话题，混词性
    POS_ONLY = "pos_only"             # 同词性，混话题
    RANDOM = "random"                 # 全库随机
    INSUFFICIENT = "insufficient"     # 候选池太小，凑不够 4 个


@dataclass(frozen=True, slots=True)
class Candidate:
    """出题时的一个候选词。

    只带挑选和展示需要的字段，不依赖 ORM —— 单测不需要数据库。
    """

    word_id: uuid.UUID
    meaning_primary: str
    topic: str | None = None
    part_of_speech: str | None = None


@dataclass(frozen=True, slots=True)
class Option:
    """展示给用户的一个选项。"""

    index: int   # 1–4，对应键盘按键
    text: str


@dataclass(frozen=True, slots=True)
class Question:
    """一道阅读题。

    ⚠️ **`correct_index` 绝不能下发给客户端** —— 否则看 network 面板就能作弊。
       判定在服务端做（见 docs/04-api-design.md §4）。
    """

    options: tuple[Option, ...]
    correct_index: int
    fallback_level: FallbackLevel

    @property
    def is_complete(self) -> bool:
        """是否凑够了 4 个选项。"""
        return len(self.options) == OPTION_COUNT


def strip_pos_prefix(meaning: str) -> str:
    """剥掉释义开头的词性前缀。

    只剥真正的词性缩写，保留 `[经]` `[计]` 这类领域标记 —— 它们是释义的一部分。

    >>> strip_pos_prefix("n. 交集, 十字路口, 交叉点")
    '交集, 十字路口, 交叉点'
    >>> strip_pos_prefix("vt. 废止, 革除")
    '废止, 革除'
    >>> strip_pos_prefix("[经] 能力, 才能")
    '[经] 能力, 才能'
    >>> strip_pos_prefix("容纳")
    '容纳'
    """
    if not meaning:
        return ""
    return _POS_PREFIX_RE.sub("", meaning, count=1).strip()


def _pick_pool(
    target: Candidate,
    pool: list[Candidate],
) -> tuple[list[Candidate], FallbackLevel]:
    """按降级链挑出可用的干扰项候选。"""
    others = [c for c in pool if c.word_id != target.word_id]

    if target.topic and target.part_of_speech:
        same_both = [
            c for c in others
            if c.topic == target.topic and c.part_of_speech == target.part_of_speech
        ]
        if len(same_both) >= DISTRACTOR_COUNT:
            return same_both, FallbackLevel.TOPIC_AND_POS

    if target.topic:
        same_topic = [c for c in others if c.topic == target.topic]
        if len(same_topic) >= DISTRACTOR_COUNT:
            return same_topic, FallbackLevel.TOPIC_ONLY

    if target.part_of_speech:
        same_pos = [c for c in others if c.part_of_speech == target.part_of_speech]
        if len(same_pos) >= DISTRACTOR_COUNT:
            return same_pos, FallbackLevel.POS_ONLY

    if len(others) >= DISTRACTOR_COUNT:
        return others, FallbackLevel.RANDOM

    # 候选池实在太小（新词库、或某话题只有一两个词）
    return others, FallbackLevel.INSUFFICIENT


def build_question(
    target: Candidate,
    pool: list[Candidate],
    rng: random.Random | None = None,
) -> Question:
    """给一个目标词生成 4 选 1。

    Args:
        target: 要考查的词
        pool:   候选词池（可以包含 target 本身，会自动排除）
        rng:    随机源。生产传 `random.Random()`，测试传固定种子的实例
                以获得可复现结果。

    Returns:
        Question。`options` 已打乱，`correct_index` 指向正确选项。

    去重规则：**释义文本相同的候选会被排除**。
    同话题里出现两个释义一样的词时，用户会看到两个相同选项 —— 那题就废了。

    >>> import random, uuid
    >>> t = Candidate(uuid.UUID(int=0), "n. 探索, 寻求", "教育", "n.")
    >>> pool = [Candidate(uuid.UUID(int=i), f"n. 释义{i}", "教育", "n.") for i in range(1, 5)]
    >>> q = build_question(t, pool, random.Random(42))
    >>> len(q.options), q.fallback_level.value
    (4, 'topic_and_pos')
    >>> q.options[q.correct_index - 1].text
    '探索, 寻求'
    """
    if rng is None:
        rng = random.Random()

    correct_text = strip_pos_prefix(target.meaning_primary)
    if not correct_text:
        raise ValueError(f"目标词 {target.word_id} 的 meaning_primary 为空")

    candidates, level = _pick_pool(target, pool)

    # 排除与正确答案**展示文本**相同的候选 —— 剥掉前缀后可能撞车
    seen = {correct_text}
    usable: list[Candidate] = []
    for c in candidates:
        text = strip_pos_prefix(c.meaning_primary)
        if text and text not in seen:
            seen.add(text)
            usable.append(c)

    if len(usable) < DISTRACTOR_COUNT:
        level = FallbackLevel.INSUFFICIENT

    chosen = rng.sample(usable, min(DISTRACTOR_COUNT, len(usable)))

    texts = [correct_text] + [strip_pos_prefix(c.meaning_primary) for c in chosen]
    rng.shuffle(texts)

    options = tuple(Option(index=i + 1, text=t) for i, t in enumerate(texts))
    correct_index = texts.index(correct_text) + 1

    return Question(options=options, correct_index=correct_index, fallback_level=level)
