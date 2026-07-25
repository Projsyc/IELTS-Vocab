"""答题事件表 —— 整个系统的事实来源。

⚠️⚠️ 铁律（见 docs/08-decisions.md ADR-002）：

    1. 这张表只 INSERT，永不 UPDATE、永不 DELETE
    2. user_progress 的每一行都必须能由这张表完整重算出来
    3. 回放必须按 answered_at（客户端时间）排序，不是 created_at 或自增 id

违反任一条，混合式事件溯源的价值就没了。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import PracticeMode


class AnswerEvent(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        # 回放某个 (user, word, mode) 的全部事件 —— 最热的查询路径
        Index("idx_events_replay", "user_id", "word_id", "mode", "answered_at"),
        # 按时间倒序拉某用户的近期答题（错题本、统计）
        Index("idx_events_user_time", "user_id", "answered_at"),
    )

    # 用自增 BIGINT 而非 UUID：事件量大，自增主键索引更紧凑。
    # 注意：id 的顺序 ≠ 答题顺序（离线补传会乱序），排序必须用 answered_at。
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    word_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("words.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[PracticeMode] = mapped_column(
        Enum(PracticeMode, name="practice_mode", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # 听写：用户拼写的内容；阅读：选项 index 的字符串，或 "unknown"（点了"不知道"）
    user_input: Mapped[str | None] = mapped_column(Text)

    # ⭐ 客户端答题时刻 —— 回放按此排序。
    # 离线答的题可能几小时后才上传，入库顺序 ≠ 真实发生顺序。
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    device_id: Mapped[str | None] = mapped_column(String(64))

    # 入库时刻（服务器时间）。与 answered_at 偏差过大可用于检测客户端时钟异常（v2）。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        mark = "✓" if self.is_correct else "✗"
        return f"<AnswerEvent {mark} {self.mode.value} @{self.answered_at:%Y-%m-%d %H:%M}>"
