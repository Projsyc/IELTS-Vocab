"""进度相关的响应模型。对应 docs/04-api-design.md §5。"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.models.enums import PracticeMode
from app.schemas.auth import CamelModel


class DailyStatsOut(CamelModel):
    answered: int
    correct: int
    accuracy: float


class ProgressSummaryOut(CamelModel):
    streak_days: int
    today: DailyStatsOut
    total_answered: int
    #: {"dictation": {"1": 50, "2": 30, ...}, "recognition": {...}}
    #: JSON 的 key 只能是字符串，所以盒子号转成了字符串
    boxes: dict[str, dict[str, int]]
    #: {"dictation": 15, "recognition": 22}
    due_now: dict[str, int]


class WrongWordOut(CamelModel):
    word_id: uuid.UUID
    word: str
    meaning_primary: str
    phonetic: str | None
    mode: PracticeMode
    wrong_count: int
    last_wrong_at: datetime
    #: 最近几次答错时的输入 —— 能看出总是怎么拼错的
    recent_inputs: list[str]


class WrongWordsPage(CamelModel):
    total: int
    limit: int
    offset: int
    items: list[WrongWordOut]


class RebuildRequest(CamelModel):
    #: 不传则重建当前用户的全部进度
    word_id: uuid.UUID | None = None


class RebuildResponse(CamelModel):
    rebuilt: int
    removed: int
    events_read: int
    duration_ms: int
