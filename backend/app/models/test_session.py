"""测试会话 —— 一次"考试"的元信息。

═══════════════════════════════════════════════════════════════════════
为什么需要这张表
═══════════════════════════════════════════════════════════════════════

测试模式（"错了就是错了"）的答题也写进 `answer_events`，靠 `is_test` 标记
与学习模式区分、回放时跳过。但要做"测试记录"页（每次考了多少题、
得多少分、错了哪些），需要能**把一批答题归为一次考试**。

所以给每次测试发一个 session id，答题时带上。

顺带一个好处：服务端能校验 session 属于当前用户，
客户端没法随便编个 id 把真实练习伪装成"不计入进度"的测试。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import PracticeMode


class TestSession(Base):
    __tablename__ = "test_sessions"
    __table_args__ = (
        Index("idx_test_sessions_user_time", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[PracticeMode] = mapped_column(
        Enum(PracticeMode, name="practice_mode", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    #: 出题范围：learned | all | topic | box
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    #: scope=topic 时的话题名；scope=box 时的盒子号（存字符串，够用）
    scope_value: Mapped[str | None] = mapped_column(String(50))
    #: 本次出了多少题。得分靠 answer_events 现算，不冗余存 ——
    #: 存了就得和事件保持一致，多一处可能不一致的地方。
    total: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TestSession {self.mode.value} {self.total}题 @{self.created_at:%Y-%m-%d %H:%M}>"
