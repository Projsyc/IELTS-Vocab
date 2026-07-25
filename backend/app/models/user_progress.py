"""学习进度表 —— answer_events 的缓存投影。

⚠️ 这张表是**缓存**。删掉整张表，从 answer_events 回放能完整重建。

    不要往这里写"事件里没有的信息"。一旦写了，"可重算"这个性质就破了，
    整个混合式事件溯源的价值也就没了。有这种需求请新增事件类型或单开一张表。

    见 docs/08-decisions.md ADR-002
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import MAX_BOX, MIN_BOX, PracticeMode


class UserProgress(Base):
    __tablename__ = "user_progress"
    __table_args__ = (
        CheckConstraint(f"box BETWEEN {MIN_BOX} AND {MAX_BOX}", name="ck_progress_box_range"),
        # 查"今天该复习哪些词" —— 最热的查询路径
        Index("idx_progress_due", "user_id", "mode", "next_review_at"),
    )

    # ⭐ 三元组主键：同一个词在听写和阅读模式下各有一套独立进度。
    #
    # 为什么："认得出"和"会拼写"是两种能力。你能一眼认出 accommodate 是"容纳"，
    # 但拼的时候可能少个 m。合并会让阅读模式的连续答对把这个词推进高盒子，
    # 导致本该反复练的拼写被误判为已掌握。
    #
    # 见 docs/08-decisions.md ADR-003
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    word_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("words.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[PracticeMode] = mapped_column(
        Enum(PracticeMode, name="practice_mode", values_callable=lambda e: [m.value for m in e]),
        primary_key=True,
    )

    # —— Leitner 状态 ——
    box: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=MIN_BOX)
    next_review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # —— 统计 ——
    # 这些也能从事件算出来，冗余存一份是为了展示时少一次聚合查询。
    # 仍然满足"可从事件完整重算"，没有破坏 ADR-002 的不变式。
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<UserProgress box={self.box} {self.mode.value} due={self.next_review_at:%Y-%m-%d}>"
