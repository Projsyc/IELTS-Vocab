"""词库与练习相关的请求/响应模型。

对应 docs/04-api-design.md §3–4。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.enums import PracticeMode
from app.schemas.auth import CamelModel


# ─────────────────────────────────────────────────────────────
# 词库
# ─────────────────────────────────────────────────────────────


class WordListOut(CamelModel):
    id: uuid.UUID
    name: str
    description: str | None
    word_count: int
    is_public: bool


class MasteryBreakdown(CamelModel):
    new: int        # 无进度记录
    learning: int   # box 1–4
    mastered: int   # box 5


class WordListStats(CamelModel):
    word_list_id: uuid.UUID
    total: int
    dictation: MasteryBreakdown
    recognition: MasteryBreakdown


# ─────────────────────────────────────────────────────────────
# 练习
# ─────────────────────────────────────────────────────────────


class ChoiceOption(CamelModel):
    """阅读模式的一个选项。

    ⚠️ 刻意**不含**"是否正确"标记 —— 否则看 network 面板就能作弊。
       判定在服务端做（见 services/practice.py）。
    """

    index: int
    text: str


class PracticeItemOut(CamelModel):
    word_id: uuid.UUID
    #: ⚠️ 听写模式下前端**不得展示**，仅用于提交后比对与高亮
    word: str
    phonetic: str | None
    meaning: str | None
    audio_url: str | None
    part_of_speech: str | None
    #: 当前盒子号，null 表示新词
    box: int | None
    #: 仅阅读模式有值，已打乱顺序
    options: list[ChoiceOption] | None


class PracticeSetOut(CamelModel):
    mode: PracticeMode
    total: int
    review_count: int
    new_count: int
    items: list[PracticeItemOut]


class CreateSessionRequest(CamelModel):
    list_id: uuid.UUID
    mode: PracticeMode
    count: int = Field(default=20, ge=1, le=200)
    scope: Literal["all", "review_only", "new_only", "topic"] = "all"
    topic: str | None = None


# ─────────────────────────────────────────────────────────────
# 答题
# ─────────────────────────────────────────────────────────────


class AnswerRequest(CamelModel):
    word_id: uuid.UUID
    mode: PracticeMode
    #: 听写：用户拼写的内容
    #: 阅读：**选中选项的文本**，或 "unknown"（点了"不知道"）
    #:
    #: ⚠️ 阅读模式回传文本而非选项 index —— 题目是无状态生成的，
    #:    服务端不保存"第几个是对的"，回传 index 无从验证。
    #:    详见 services/practice.py 的 _judge_recognition。
    user_input: str = Field(max_length=500)
    #: ⭐ 客户端答题时刻。回放按此排序，不是入库时间（ADR-002）
    answered_at: datetime
    device_id: str | None = Field(default=None, max_length=64)


class DiffCharOut(CamelModel):
    pos: int
    char: str
    status: str
    expected: str | None


class ProgressOut(CamelModel):
    box: int
    next_review_at: datetime
    correct_count: int
    wrong_count: int


class AnswerResponse(CamelModel):
    is_correct: bool
    correct_answer: str
    #: 仅听写模式返回，用于错误位置高亮
    diff: list[DiffCharOut] | None
    progress: ProgressOut
    #: True 表示这条事件比已有记录更早（离线补传），走了全量回放
    was_replayed: bool
