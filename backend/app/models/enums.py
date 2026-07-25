"""共享的枚举与常量。

放在 models/ 下是因为 practice_mode 需要作为 PostgreSQL ENUM 类型建表，
但业务代码也要用，所以定义在这里统一导入。
"""

import enum


class PracticeMode(str, enum.Enum):
    """练习模式。

    继承 str 是为了让 Pydantic 序列化成字符串而不是 "PracticeMode.DICTATION"。

    ⚠️ 听写和阅读的进度完全独立 —— user_progress 的主键含 mode。
       原因见 docs/08-decisions.md ADR-003。
    """

    DICTATION = "dictation"      # 听写：播音频 → 拼写
    RECOGNITION = "recognition"  # 阅读：看词 → 4 选 1 中文释义


# Leitner 盒子复习间隔（天）。改这里等于改算法，改完需重放事件重建进度。
# 见 docs/08-decisions.md ADR-004
BOX_INTERVALS: dict[int, int] = {
    1: 1,
    2: 2,
    3: 4,
    4: 7,
    5: 15,
}

MAX_BOX = 5
MIN_BOX = 1
