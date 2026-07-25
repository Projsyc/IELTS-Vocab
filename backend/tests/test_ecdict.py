"""ECDICT 解析函数单测。

测试数据全部来自 docs/09-wordlist-research.md 里的**真实实测样本**，
不是编的 —— 这样测试挂了能确定是代码问题，不是假设问题。
"""

import csv
from pathlib import Path

import pytest

from app.scripts.ecdict import (
    audio_filename,
    build_meanings,
    clean_ecdict_phonetic,
    derive_difficulty,
    has_ielts_tag,
    is_redundant_inflection,
    normalize_api_phonetic,
    parse_int,
    parse_lemma,
    parse_part_of_speech,
    split_senses,
)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _wordlist_path() -> Path:
    """定位真实词表，优先用提交进仓库的雅思子集。

    子集是 git 追踪的（1.5MB，见 ADR-011），所以这些测试在 fresh clone 上
    也会真正执行，不会静默跳过。
    """
    subset = _DATA_DIR / "ecdict-ielts.csv"
    if subset.exists():
        return subset
    full = _DATA_DIR / "ecdict.csv"
    if full.exists():
        return full
    pytest.skip(f"找不到词表数据（{subset} 或 {full}）")


# ─────────────────────────────────────────────────────────────
# ielts 标签识别
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("tag", "expected"), [
    ("cet6 toefl ielts", True),
    ("zk gk cet4 ky toefl ielts", True),
    ("ielts gre", True),
    ("ielts", True),
    ("cet6 toefl gre", False),
    ("", False),
    (None, False),
    # 关键：不能用子串匹配。若将来出现这类标签不应误判
    ("ielts-plus", False),
    ("preielts", False),
])
def test_has_ielts_tag(tag, expected):
    assert has_ielts_tag(tag) is expected


# ─────────────────────────────────────────────────────────────
# 释义拆分（多义占 75.1%）
# ─────────────────────────────────────────────────────────────

def test_split_senses_single():
    assert split_senses("n. 缩写词, 缩写, 缩短, 节略") == ["n. 缩写词, 缩写, 缩短, 节略"]


def test_split_senses_multi():
    """分隔符是**字面两个字符** \\n，不是真换行。"""
    raw = "n. 能力, 才干\\n[经] 能力, 才能"
    assert split_senses(raw) == ["n. 能力, 才干", "[经] 能力, 才能"]


def test_split_senses_real_sample_abstract():
    """abstract 实测有 4 个义项。"""
    raw = "a. 抽象的, 深奥的\\nn. 摘要, 抽象概念\\nvt. 摘要, 提炼, 使抽象化\\n[计] 摘录; 摘要; 抽象"
    senses = split_senses(raw)
    assert len(senses) == 4
    assert senses[0] == "a. 抽象的, 深奥的"
    assert senses[-1] == "[计] 摘录; 摘要; 抽象"


def test_split_senses_empty():
    assert split_senses("") == []
    assert split_senses("\\n\\n") == []


def test_build_meanings_uses_first_sense_as_primary():
    """首义必须是第一个义项 —— 阅读模式选项用它。"""
    raw = "a. 抽象的, 深奥的\\nn. 摘要, 抽象概念\\nvt. 摘要, 提炼"
    full, primary = build_meanings(raw)
    assert primary == "a. 抽象的, 深奥的"
    assert full == "a. 抽象的, 深奥的 / n. 摘要, 抽象概念 / vt. 摘要, 提炼"
    # 首义必须显著短于完整释义 —— 这是分字段存的全部理由
    assert len(primary) < len(full)


def test_build_meanings_single_sense_identical():
    """单义词的完整释义和首义相同，不该报错。"""
    full, primary = build_meanings("n. 缩写词, 缩写")
    assert full == primary == "n. 缩写词, 缩写"


def test_build_meanings_empty():
    assert build_meanings("") == ("", "")


# ─────────────────────────────────────────────────────────────
# 词性解析（实测 99.5% 可解析）
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("translation", "expected"), [
    ("n. 缩写词, 缩写, 缩短, 节略", "n."),
    ("a. 反常的, 不规则的", "a."),
    ("vt. 废止, 革除, 消灭", "vt."),
    ("vi. 相符合", "vi."),
    ("adv. 突然地", "adv."),
    ("prep. 关于", "prep."),
    ("abbr. 略语", "abbr."),
    ("num. 三", "num."),
    ("pl. 复数形式", "pl."),
    # 多义词只看第一个义项
    ("n. 能力, 才干\\n[经] 能力, 才能", "n."),
    # 领域标记开头 → 无词性，这是那 0.5%
    ("[经] 能力, 才能", None),
    ("容纳", None),
    ("", None),
])
def test_parse_part_of_speech(translation, expected):
    assert parse_part_of_speech(translation) == expected


# ─────────────────────────────────────────────────────────────
# 音标清洗（兜底用，非标准 IPA）
# ─────────────────────────────────────────────────────────────

def test_clean_phonetic_fixes_cyrillic():
    """核心职责：把西里尔 ә(U+04D9) 换成 IPA ə(U+0259)。"""
    result = clean_ecdict_phonetic("ә'biliti")
    assert "ә" not in result, "西里尔字符必须被替换"
    assert "ә" not in result
    assert "ə" in result


def test_clean_phonetic_fixes_ukrainian_ie():
    result = clean_ecdict_phonetic("brєd")
    assert "є" not in result
    assert "ɛ" in result


def test_clean_phonetic_stress_and_length_marks():
    """' → ˈ 主重音、. → ˌ 次重音、: → ː 长音。

    次重音这层是对照 abbreviation 的标准 IPA /əˌbriːviˈeɪʃən/ 反推确认的。
    """
    assert clean_ecdict_phonetic("ә.bri:vi'eiʃәn") == "/əˌbriːviˈeiʃən/"


def test_clean_phonetic_real_samples():
    """调研时的真实样本。"""
    cases = {
        "ә'biliti": "/əˈbiliti/",
        "æb'nɒ:mәl": "/æbˈnɒːməl/",
        "ә'kɒmәdeit": "/əˈkɒmədeit/",
        ".æbә'ridʒәnәl": "/ˌæbəˈridʒənəl/",
    }
    for raw, expected in cases.items():
        assert clean_ecdict_phonetic(raw) == expected, raw


def test_clean_phonetic_no_double_slashes():
    """已带斜杠的输入不该变成 //xxx//。"""
    assert clean_ecdict_phonetic("/slʌm/") == "/slʌm/"
    assert clean_ecdict_phonetic("[slʌm]") == "/slʌm/"


def test_clean_phonetic_empty():
    assert clean_ecdict_phonetic(None) is None
    assert clean_ecdict_phonetic("") is None
    assert clean_ecdict_phonetic("   ") is None
    assert clean_ecdict_phonetic("//") is None


def test_clean_phonetic_is_not_standard_ipa():
    """明确记录一个**已知局限**，防止后人误以为这函数产出标准 IPA。

    ability 的标准 IPA 是 /əˈbɪlɪti/（ɪ），
    但 ECDICT 记的是 i，规则无法判断该转成 ɪ 还是 i。
    这就是音标优先从 API 取的原因。
    """
    assert clean_ecdict_phonetic("ә'biliti") == "/əˈbiliti/"
    assert clean_ecdict_phonetic("ә'biliti") != "/əˈbɪlɪti/"


# ─────────────────────────────────────────────────────────────
# API 音标规范化
# ─────────────────────────────────────────────────────────────

def test_normalize_api_phonetic_turned_r():
    """API 用 ɹ(U+0279)，换成普通 r。"""
    assert normalize_api_phonetic("/ˈʌpɹaɪt/") == "/ˈʌpraɪt/"
    assert normalize_api_phonetic("/ɹiəˈʃʊə(ɹ)/") == "/riəˈʃʊə(r)/"


def test_normalize_api_phonetic_strips_syllable_dots():
    """删掉音节分隔点。

    ⚠️ 关键区别：`.` 在 ECDICT 里是**次重音**（转 ˌ），
       在 API 的 IPA 里是**音节分隔**（删掉）。同一字符，两种含义。

    真实样本来自 40 词试跑。
    """
    assert normalize_api_phonetic("/əˈ.bɪl.ɪ.ti/") == "/əˈbɪlɪti/"
    assert normalize_api_phonetic("/ə.ˈkaʊnt/") == "/əˈkaʊnt/"
    assert normalize_api_phonetic("/əˈbreɪ.ʒn̩/") == "/əˈbreɪʒn̩/"
    assert normalize_api_phonetic("/ˌæb.əˈrɪd͡ʒ.n̩.l̩/") == "/ˌæbəˈrɪd͡ʒn̩l̩/"


def test_dot_means_different_things_in_each_source():
    """把两个来源的 `.` 语义差异钉死，防止将来有人"统一"这两个函数。"""
    # ECDICT: . 是次重音 → ˌ
    assert clean_ecdict_phonetic(".æbә'ridʒәnәl") == "/ˌæbəˈridʒənəl/"
    # API: . 是音节分隔 → 删掉
    assert normalize_api_phonetic("/ˌæb.əˈrɪdʒ.n̩.l̩/") == "/ˌæbəˈrɪdʒn̩l̩/"


def test_normalize_api_phonetic_adds_slashes():
    assert normalize_api_phonetic("slʌm") == "/slʌm/"


def test_normalize_api_phonetic_keeps_single_slashes():
    assert normalize_api_phonetic("/ˈθɔːtfəl/") == "/ˈθɔːtfəl/"


def test_normalize_api_phonetic_empty():
    assert normalize_api_phonetic(None) is None
    assert normalize_api_phonetic("") is None
    assert normalize_api_phonetic("/") is None
    assert normalize_api_phonetic("/./") is None   # 只剩音节点也算空


# ─────────────────────────────────────────────────────────────
# 难度推导
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("tags", "expected"), [
    # 含中考/高考/四级 → 最简单
    ("zk gk cet4 ky toefl ielts", 1),
    ("gk cet4 cet6 ky toefl ielts", 1),
    ("zk ielts", 1),
    ("cet4 ielts", 1),
    # 含六级/考研/托福（但无更基础的）→ 中等
    ("cet6 toefl ielts", 2),
    ("ky ielts", 2),
    ("cet6 ielts gre", 2),
    # 只在雅思/GRE 出现 → 最难
    ("ielts", 3),
    ("ielts gre", 3),
])
def test_derive_difficulty(tags, expected):
    assert derive_difficulty(tags) == expected


def test_derive_difficulty_defaults_to_hard():
    """标签缺失时保守判为难 —— 宁可多练。"""
    assert derive_difficulty(None) == 3
    assert derive_difficulty("") == 3


# ─────────────────────────────────────────────────────────────
# 数字字段
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "expected"), [
    ("6548", 6548),
    ("1", 1),
    # ECDICT 用 "0" 表示"无排名"，语义上应是 NULL
    ("0", None),
    ("", None),
    (None, None),
    ("abc", None),
    ("-5", None),
    ("3.14", None),
])
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected


# ─────────────────────────────────────────────────────────────
# 音频文件名
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("word", "expected"), [
    ("accommodate", "accommodate.mp3"),
    ("Sydney", "sydney.mp3"),
    ("in spite of", "in_spite_of.mp3"),
    ("self-esteem", "self-esteem.mp3"),
    ("o'clock", "o_clock.mp3"),
    ("  spaced  ", "spaced.mp3"),
])
def test_audio_filename(word, expected):
    assert audio_filename(word) == expected


def test_audio_filename_collapses_underscores():
    """连续的非法字符不该产生一串下划线。

    注意连字符是**刻意保留**的（self-esteem.mp3 比 self_esteem.mp3 好读），
    所以 "a -- b" → "a_--_b.mp3" 是预期行为，不是 bug。
    """
    assert audio_filename("a   b") == "a_b.mp3"
    assert audio_filename("a???b") == "a_b.mp3"


def test_audio_filename_never_empty():
    """全是非法字符时要有兜底，不能返回 '.mp3'。"""
    assert audio_filename("???") == "unknown.mp3"
    assert audio_filename("") == "unknown.mp3"


def test_audio_filename_no_collision_on_real_wordlist():
    """⚠️ 关键测试：真实词表里不能有两个词映射到同一文件名。

    碰撞会导致音频互相覆盖 —— 用户听到的是另一个单词的发音，
    而且不会有任何报错，属于最难查的那种 bug。

    读的是**提交进仓库的子集**，所以 fresh clone 上也会真正执行，不会静默跳过。
    """
    csv_path = _wordlist_path()
    csv.field_size_limit(10**9)
    seen: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []

    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not has_ielts_tag(row.get("tag")):
                continue
            word = row["word"]
            fname = audio_filename(word)
            if fname in seen and seen[fname] != word:
                collisions.append((fname, seen[fname], word))
            seen[fname] = word

    assert not collisions, (
        f"发现 {len(collisions)} 组文件名碰撞，前 5 组：{collisions[:5]}"
    )


# ─────────────────────────────────────────────────────────────
# 屈折形式识别与剔除
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("exchange", "expected"), [
    # 0: 标记本词是某个原型的变形
    ("0:accommodation/1:s", "accommodation"),
    ("0:account/1:i", "account"),
    ("1:s/0:activity", "activity"),          # 位置不固定
    # 只有变形列表、没有 0: → 本词就是原型
    ("d:accommodated/p:accommodated/3:accommodates", None),
    ("s:animals", None),
    ("", None),
    (None, None),
    ("0:", None),                             # 空原型
])
def test_parse_lemma(exchange, expected):
    assert parse_lemma(exchange) == expected


def test_redundant_inflection_when_lemma_present():
    """原型也在词表里 → 冗余，该剔。"""
    vocab = {"accommodation", "accommodations"}
    assert is_redundant_inflection("accommodations", "0:accommodation/1:s", vocab) is True


def test_not_redundant_when_lemma_absent():
    """原型不在词表里 → 剔了就是丢词，必须保留。

    实测有 156 个这样的词，占屈折形式的 36%。
    """
    vocab = {"accommodations"}          # 只有复数被收录
    assert is_redundant_inflection("accommodations", "0:accommodation/1:s", vocab) is False


def test_not_redundant_for_base_form():
    """原型词本身不该被当成屈折形式。"""
    vocab = {"accommodate", "accommodation"}
    assert is_redundant_inflection(
        "accommodate", "d:accommodated/p:accommodated/3:accommodates", vocab
    ) is False


def test_not_redundant_without_exchange():
    assert is_redundant_inflection("abolish", None, {"abolish"}) is False
    assert is_redundant_inflection("abolish", "", {"abolish"}) is False


def test_not_redundant_when_lemma_equals_word():
    """自指的 exchange（数据脏）不该导致把自己剔掉。"""
    assert is_redundant_inflection("actions", "0:actions", {"actions"}) is False


def test_inflection_filter_on_real_wordlist():
    """在真实词表上验证剔除规模，并确认没有把原型误剔。

    实测预期：5,040 → 剔 272 → 剩 4,768。

    ⚠️ 这些断言是**刻意写死**的：如果上游 ECDICT 更新导致数字变化，
       这个测试会失败，提醒你去复核 docs/09 和 ADR-007/010 里的实测数字。
       这是保护，不是脆弱。
    """
    csv_path = _wordlist_path()
    csv.field_size_limit(10**9)
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if has_ielts_tag(row.get("tag")):
                rows.append((row["word"].strip(), row.get("exchange")))

    vocab = {w for w, _ in rows}
    dropped = [w for w, ex in rows if is_redundant_inflection(w, ex, vocab)]
    kept = [w for w, ex in rows if not is_redundant_inflection(w, ex, vocab)]

    assert len(rows) == 5040, f"ielts 词条数变了：{len(rows)}（上游数据可能更新了）"
    assert len(dropped) == 272, f"剔除数量与实测不符：{len(dropped)}"
    assert len(kept) == 4768, f"保留数量与实测不符：{len(kept)}"

    # 关键：被剔的词，它的原型必须还在保留集里 —— 否则就是丢词
    kept_set = set(kept)
    for word in dropped:
        exchange = next(ex for w, ex in rows if w == word)
        lemma = parse_lemma(exchange)
        assert lemma in kept_set, f"剔了 {word} 但原型 {lemma} 也不在保留集里"


def test_committed_subset_exists():
    """仓库内必须有雅思子集 —— 这是 fresh clone 能离线构建的前提（ADR-011）。"""
    subset = Path(__file__).resolve().parents[1] / "data" / "ecdict-ielts.csv"
    assert subset.exists(), (
        f"缺少 {subset}。跑 python -m app.scripts.export_ielts_subset 重新导出。"
    )
    size_mb = subset.stat().st_size / 1e6
    assert 1.0 < size_mb < 5.0, f"子集大小异常：{size_mb:.1f} MB"
