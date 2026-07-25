"""ECDICT 数据解析 —— 全部纯函数，方便单测。

数据来源与实测覆盖率见 docs/09-wordlist-research.md。
这里只做"字符串进、字符串出"的转换，不碰数据库和网络。
"""

import re

# ─────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────

#: ECDICT 的 tag 字段里表示雅思大纲的标签
IELTS_TAG = "ielts"

#: 多义项在 ECDICT 的 translation 里用**字面两个字符** \n 分隔（不是真换行）
SENSE_SEP_RAW = "\\n"

#: 我们对外呈现时用的分隔符
SENSE_SEP = " / "

#: 词性前缀，如 "n. 缩写词" / "vt. 废止" / "abbr. 略语"
#: 允许多段（"a.n." 这种少见但存在）
_POS_RE = re.compile(r"^\s*((?:[a-z]+\.)+)")

#: ECDICT 音标里混入的西里尔字母 → 正确的 IPA 字符
#: 实测：ә(U+04D9) 出现 3,201 次，є(U+0454) 57 次，影响 49.4% 词条
_CYRILLIC_FIX = {
    "ә": "ə",  # ә CYRILLIC SCHWA        → ə LATIN SCHWA
    "є": "ɛ",  # є CYRILLIC UKRAINIAN IE → ɛ LATIN OPEN E
}

#: dictionaryapi.dev 返回的 IPA 用 ɹ(U+0279 turned r)，学习者更熟悉普通 r
_API_IPA_FIX = {
    "ɹ": "r",  # ɹ → r
}

#: 难度分级依据的考试标签。含更基础的标签 → 更简单。
#: 规则来自 docs/03-data-model.md §6，可调整。
_BASIC_TAGS = frozenset({"zk", "gk", "cet4"})     # 中考 / 高考 / 四级
_MID_TAGS = frozenset({"cet6", "ky", "toefl"})    # 六级 / 考研 / 托福


# ─────────────────────────────────────────────────────────────
# 解析函数
# ─────────────────────────────────────────────────────────────


def has_ielts_tag(tag_field: str | None) -> bool:
    """判断 ECDICT 的 tag 字段是否含 ielts 标签。

    tag 是空格分隔的多标签，如 "cet6 toefl ielts gre"。
    必须按空格切分后精确匹配 —— 不能用 `"ielts" in tag`，
    否则将来若出现 "ielts-plus" 这类标签会误判。
    """
    if not tag_field:
        return False
    return IELTS_TAG in tag_field.split()


def split_senses(translation: str) -> list[str]:
    """把 ECDICT 的 translation 拆成义项列表。

    >>> split_senses("n. 能力, 才干\\\\n[经] 能力, 才能")
    ['n. 能力, 才干', '[经] 能力, 才能']
    """
    if not translation:
        return []
    parts = (p.strip() for p in translation.split(SENSE_SEP_RAW))
    return [p for p in parts if p]


def build_meanings(translation: str) -> tuple[str, str]:
    """返回 (完整释义, 首义)。

    完整释义用 " / " 连接所有义项；首义只取第一个。

    为什么要分开存：完整释义中位数 27 字、最长 169 字，塞进 4 选 1 太挤，
    且长度差异本身会泄露答案；首义中位数仅 14 字，长度均匀。
    见 docs/08-decisions.md ADR-008。
    """
    senses = split_senses(translation)
    if not senses:
        return "", ""
    return SENSE_SEP.join(senses), senses[0]


def parse_part_of_speech(translation: str) -> str | None:
    """从释义前缀解析词性。实测 99.5% 可解析。

    ECDICT 的 pos 字段对 ielts 子集**全为空**，但 translation 以词性缩写开头。

    >>> parse_part_of_speech("n. 缩写词, 缩写")
    'n.'
    >>> parse_part_of_speech("vt. 废止, 革除")
    'vt.'
    >>> parse_part_of_speech("[经] 能力")      # 领域标记开头，无词性
    """
    senses = split_senses(translation)
    if not senses:
        return None
    m = _POS_RE.match(senses[0])
    return m.group(1) if m else None


def clean_ecdict_phonetic(phonetic: str | None) -> str | None:
    """清洗 ECDICT 音标，作为 API 拿不到 IPA 时的兜底（约 4% 的词用到）。

    ⚠️ 这**不是**标准 IPA，只是把明显错的字符修掉、记号统一。
       ECDICT 用简化记音体系（i vs ɪ、ei vs eɪ、әu vs əʊ），
       规则转换成标准 IPA 已实测不可行（0/8 一致）——
       所以音标优先从 dictionaryapi.dev 取。详见 docs/09-wordlist-research.md §3。

    做的事：
      1. 西里尔 ә→ə、є→ɛ
      2. ' → ˈ（主重音）、. → ˌ（次重音，经对照 abbreviation 确认）
      3. : → ː（长音）
      4. 外层加 / /

    >>> clean_ecdict_phonetic("ә.bri:vi'eiʃәn")
    '/əˌbriːviˈeiʃən/'
    """
    if not phonetic:
        return None

    p = phonetic.strip()
    if not p:
        return None

    # 去掉可能已有的外层包裹，避免出现 //xxx//
    p = p.strip("/[]").strip()
    if not p:
        return None

    for bad, good in _CYRILLIC_FIX.items():
        p = p.replace(bad, good)

    p = p.replace("'", "ˈ")   # ' → ˈ 主重音
    p = p.replace(".", "ˌ")   # . → ˌ 次重音
    p = re.sub(r":+", "ː", p)  # : → ː 长音（连续冒号也归一）
    p = re.sub(r"\s+", "", p)       # 音标内不留空格

    return f"/{p}/" if p else None


def normalize_api_phonetic(phonetic: str | None) -> str | None:
    """规范化 dictionaryapi.dev 返回的 IPA。

    做三件事：
      1. ɹ(U+0279 turned r) → r，学习者更熟悉
      2. **删掉音节分隔点 `.`**
      3. 确保外层有且只有一层 / /

    ⚠️ 关于第 2 点 —— `.` 在两个数据源里含义完全不同，绝不能混用同一套规则：

         ECDICT 记音：`.` = 次重音   → 转成 ˌ（见 clean_ecdict_phonetic）
         API 的 IPA： `.` = 音节分隔 → 直接删掉

       实测 45% 的 API 音标带音节点、55% 不带，格式不统一；且出现过
       `/əˈ.bɪl.ɪ.ti/` 这种重音符紧跟音节点的瑕疵。学习者看音标关心的是
       读音和重音，音节边界是噪音，所以统一删掉。

    >>> normalize_api_phonetic("/ˈɹɛpɹɪˈzɛntətɪv/")
    '/ˈrɛprɪˈzɛntətɪv/'
    >>> normalize_api_phonetic("/əˈ.bɪl.ɪ.ti/")
    '/əˈbɪlɪti/'
    >>> normalize_api_phonetic("slʌm")
    '/slʌm/'
    """
    if not phonetic:
        return None

    p = phonetic.strip().strip("/[]").strip()
    if not p:
        return None

    for bad, good in _API_IPA_FIX.items():
        p = p.replace(bad, good)

    p = p.replace(".", "")          # 音节分隔点（≠ ECDICT 的次重音）
    p = re.sub(r"\s+", "", p)

    return f"/{p}/" if p else None


def derive_difficulty(tag_field: str | None) -> int:
    """从考试标签推导难度：1 易 / 2 中 / 3 难。

    规则（见 docs/03-data-model.md §6）：
      含 中考/高考/四级        → 1  这些词在更早的考试就要求掌握
      含 六级/考研/托福        → 2
      其余（仅 ielts 或 +gre） → 3  只在雅思/GRE 出现的，通常最生僻

    >>> derive_difficulty("zk gk cet4 ky toefl ielts")
    1
    >>> derive_difficulty("cet6 toefl ielts")
    2
    >>> derive_difficulty("ielts gre")
    3
    """
    tags = set((tag_field or "").split())
    if tags & _BASIC_TAGS:
        return 1
    if tags & _MID_TAGS:
        return 2
    return 3


def parse_int(value: str | None) -> int | None:
    """把 ECDICT 的数字字段转 int。空串、非数字、0 都返回 None。

    ECDICT 里 frq/bnc 缺失时是 "0" 而非空 —— 0 在语义上不是"排名第 0"，
    而是"没有排名"，所以要转成 NULL。
    """
    if not value:
        return None
    v = value.strip()
    if not v.isdigit():
        return None
    n = int(v)
    return n if n > 0 else None


def parse_lemma(exchange: str | None) -> str | None:
    """从 ECDICT 的 exchange 字段取出该词的原型（lemma）。

    exchange 用 "/" 分隔多段，每段是 "标记:值"：
        0:  该词的原型（说明本词是个屈折形式）
        p:  过去式    d: 过去分词    i: 现在分词
        3:  第三人称单数   s: 复数   r/t: 比较级/最高级

    只有 `0:` 表示"本词是别的词的变形"。

    >>> parse_lemma("0:accommodation/1:s")
    'accommodation'
    >>> parse_lemma("d:accommodated/p:accommodated/3:accommodates")
    """
    if not exchange:
        return None
    for part in exchange.split("/"):
        if part.startswith("0:"):
            lemma = part[2:].strip()
            return lemma or None
    return None


def is_redundant_inflection(word: str, exchange: str | None, all_words: set[str]) -> bool:
    """判断该词是否是"真冗余"的屈折形式 —— 即它的原型也在词表里。

    为什么要剔：`accommodations` 和 `accommodation` 同时存在时，
    让用户分别背两次没有额外学习价值；而且屈折形式在
    dictionaryapi.dev 里查不到，音标和真人音频都会缺失。

    为什么**只剔原型也在表里的**：有 156 个词的原型不在词表中
    （只以屈折形式被收录），剔了就是纯丢词。

    >>> is_redundant_inflection("accommodations", "0:accommodation/1:s",
    ...                         {"accommodation", "accommodations"})
    True
    >>> is_redundant_inflection("accommodations", "0:accommodation/1:s",
    ...                         {"accommodations"})          # 原型不在表里
    False
    >>> is_redundant_inflection("accommodate", "d:accommodated", {"accommodate"})
    False
    """
    lemma = parse_lemma(exchange)
    if not lemma or lemma == word:
        return False
    return lemma in all_words


def audio_filename(word: str) -> str:
    """生成音频文件名。

    单词里可能有空格、连字符、撇号（如 "in spite of"、"self-esteem"、"o'clock"），
    直接当文件名不安全，做一次保守替换。

    >>> audio_filename("in spite of")
    'in_spite_of.mp3'
    >>> audio_filename("o'clock")
    'o_clock.mp3'
    """
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", word.strip().lower())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return f"{safe or 'unknown'}.mp3"
