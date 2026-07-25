"""星标 —— 用户标记"重点关注"的词。

═══════════════════════════════════════════════════════════════════════
为什么单独一张表，而不是塞进 user_progress
═══════════════════════════════════════════════════════════════════════

ADR-002 的铁律：`user_progress` 的每一行都必须能从 `answer_events`
**完整重算**出来。

星标是**用户主动打的书签**，不是答题行为的推论 —— 它不可能从事件算出来。
塞进 user_progress 就破坏了那条不变式（`POST /progress/rebuild` 会把它抹掉）。

所以单独存一张表。这也是那条铁律的正确用法：
遇到"想往 progress 里塞点东西"的需求时，停下来想想它是不是事件的推论；
不是的话就新开一张表。

⚠️ 星标**不区分模式** —— 标记的是"这个词我要重点关注"，
   而不是"这个词的听写要重点关注"。这与 user_progress 的三元组主键不同，
   是刻意的：书签是关于词本身的。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WordStar(Base):
    __tablename__ = "word_stars"
    __table_args__ = (
        Index("idx_word_stars_user", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    word_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("words.id", ondelete="CASCADE"), primary_key=True
    )
    #: 可选备注 —— 记下"为什么标这个词"
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<WordStar user={self.user_id} word={self.word_id}>"
