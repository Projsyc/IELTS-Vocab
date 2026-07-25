"""雅思话题体系 —— 阅读模式干扰项的分组依据。

为什么需要这个：阅读模式要从**同话题**内抽 3 个干扰项。
如果全库随机，`apple` 会配上"量子力学"这种选项，题就白做了。
ECDICT 没有话题字段，任何现成数据源也没有，只能用 LLM 打标。

分类沿用雅思写作/口语的常见话题划分。
"""

from __future__ import annotations

#: 兜底话题 —— 给话题中立的词（abundant / accurate / consist 这类）。
#: **必须存在**：强行把 `accurate` 塞进"教育"或"科技"只会产出垃圾标签，
#: 反而让干扰项质量下降。
TOPIC_GENERAL = "通用/抽象"

#: 20 个雅思常见话题 + 1 个兜底。
#: 顺序即 prompt 里给 LLM 的顺序，改动会影响打标结果，改了要重新抽样校验。
TOPICS: tuple[str, ...] = (
    "教育",
    "环境",
    "科技",
    "健康与医疗",
    "工作与职业",
    "商业与经济",
    "政府与社会",
    "法律与犯罪",
    "文化与传统",
    "媒体与广告",
    "交通",
    "城市与住房",
    "家庭与人际",
    "旅游",
    "体育与休闲",
    "艺术",
    "食物与农业",
    "动物与自然",
    "语言",
    "全球化",
    TOPIC_GENERAL,
)

TOPIC_SET = frozenset(TOPICS)

#: 给 LLM 的话题说明。写清边界能显著减少误标 ——
#: 实测不加说明时 `bank`（银行/河岸）之类的多义词很容易被标进"环境"。
TOPIC_HINTS: dict[str, str] = {
    "教育": "学校、学习、考试、教学、学术研究、学位",
    "环境": "污染、气候变化、能源、资源、生态、可持续",
    "科技": "计算机、互联网、工程、发明、机械、通信技术",
    "健康与医疗": "疾病、治疗、医院、营养、心理健康、药物",
    "工作与职业": "就业、职位、薪水、技能、办公、劳资",
    "商业与经济": "贸易、金融、投资、市场、企业、消费",
    "政府与社会": "政治、政策、选举、福利、公共服务、社会问题",
    "法律与犯罪": "法律、法庭、犯罪、警察、监狱、权利",
    "文化与传统": "习俗、宗教、节庆、历史、传统价值",
    "媒体与广告": "新闻、报刊、电视、社交媒体、宣传、广告",
    "交通": "汽车、道路、公共交通、航空、航运、通勤",
    "城市与住房": "城市化、建筑、住宅、社区、基础设施、土地",
    "家庭与人际": "亲属、婚姻、育儿、朋友、社交关系、情感",
    "旅游": "旅行、度假、景点、酒店、观光",
    "体育与休闲": "运动、比赛、健身、娱乐、爱好、休息",
    "艺术": "绘画、音乐、文学、戏剧、设计、创作",
    "食物与农业": "饮食、烹饪、作物、耕作、畜牧、食品工业",
    "动物与自然": "动物、植物、地理地貌、天气、自然现象",
    "语言": "词汇、语法、翻译、沟通表达、读写",
    "全球化": "国际关系、移民、跨国、文化交流、世界性问题",
    TOPIC_GENERAL: (
        "话题中立的通用词汇 —— 抽象概念、程度副词、常见动词、"
        "逻辑连接、数量描述等，不属于以上任何具体领域"
    ),
}


def is_valid_topic(topic: str | None) -> bool:
    """判断 LLM 返回的话题是否在白名单内。

    LLM 经常自作主张返回近似值（"教育类"、"Education"、"科技/互联网"），
    这些都必须拒掉 —— 否则数据库里会出现几十个只用一次的杂牌话题，
    干扰项分组就散了。

    >>> is_valid_topic("教育")
    True
    >>> is_valid_topic("教育类")
    False
    >>> is_valid_topic(None)
    False
    """
    return bool(topic) and topic in TOPIC_SET


def normalize_topic(topic: str | None) -> str | None:
    """尽量把 LLM 的输出对齐到白名单，对不上返回 None。

    只做**保守**的清理：去空格、去引号、去常见后缀。
    不做模糊匹配 —— 猜错了比留空更糟，留空至少能通过
    `topic IS NULL` 被下一轮重跑捡起来。

    >>> normalize_topic(" 教育 ")
    '教育'
    >>> normalize_topic("「科技」")
    '科技'
    >>> normalize_topic("教育类")
    '教育'
    >>> normalize_topic("Education")
    """
    if not topic:
        return None

    t = topic.strip().strip("\"'「」『』【】()（）[]<>《》").strip()
    if not t:
        return None

    if t in TOPIC_SET:
        return t

    # 去掉 LLM 爱加的后缀："教育类" / "教育话题" / "教育领域"
    for suffix in ("类", "话题", "领域", "方面", "相关"):
        if t.endswith(suffix):
            stripped = t[: -len(suffix)].strip()
            if stripped in TOPIC_SET:
                return stripped

    return None


def topic_list_for_prompt() -> str:
    """生成 prompt 里的话题清单（带边界说明）。"""
    lines = []
    for i, topic in enumerate(TOPICS, 1):
        lines.append(f"{i}. {topic} —— {TOPIC_HINTS[topic]}")
    return "\n".join(lines)
