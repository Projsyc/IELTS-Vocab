"""ORM 模型统一导出。

⚠️ Alembic 通过 Base.metadata 发现表结构，所以每个模型都必须在这里 import，
   否则 autogenerate 会以为那张表不存在，生成一个"删表"的迁移。
"""

from app.models.answer_event import AnswerEvent
from app.models.enums import BOX_INTERVALS, MAX_BOX, MIN_BOX, PracticeMode
from app.models.test_session import TestSession
from app.models.user import User
from app.models.user_progress import UserProgress
from app.models.word import Word, WordList
from app.models.word_star import WordStar

__all__ = [
    "BOX_INTERVALS",
    "MAX_BOX",
    "MIN_BOX",
    "AnswerEvent",
    "PracticeMode",
    "TestSession",
    "User",
    "UserProgress",
    "Word",
    "WordList",
    "WordStar",
]
