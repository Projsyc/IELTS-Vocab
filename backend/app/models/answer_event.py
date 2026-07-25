"""答题事件表 —— 整个系统的事实来源。

⚠️⚠️ 铁律（见 docs/08-decisions.md ADR-002）：

    1. 这张表只 INSERT，永不 UPDATE、永不 DELETE
    2. user_progress 的每一行都必须能由这张表完整重算出来
    3. 回放必须按 answered_at（客户端时间）排序，不是 created_at 或自增 id

违反任一条，混合式事件溯源的价值就没了。

═══════════════════════════════════════════════════════════════════════
两类"特殊事件"（ADR-013）
═══════════════════════════════════════════════════════════════════════

**测试事件**（`is_test = true`）
    测试模式"错了就是错了"，不进 Leitner 循环。
    这类事件照常记录（要出成绩、进错题本），但**回放时跳过**。

**更正事件**（`corrects_event_id` 非空）
    用户点"其实我会"（拼错一个字母但确实掌握）时追加一条，
    指向被更正的那次答题。

    ⚠️ 这不是修改历史 —— 原事件原封不动，回放时把被指向的事件
       视为答对。这是事件溯源处理"事后更正"的标准做法：
       **追加一条更正，而不是改写过去**。

    更正事件本身**不参与 Leitner 转移**（它不是一次答题）。
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
        # 按测试会话聚合（测试记录页）
        Index("idx_events_test_session", "test_session_id"),
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

    # 听写：用户拼写的内容；阅读：选中选项的文本，或 "unknown"（点了"不知道"）
    user_input: Mapped[str | None] = mapped_column(Text)

    # ⭐ 客户端答题时刻 —— 回放按此排序。
    # 离线答的题可能几小时后才上传，入库顺序 ≠ 真实发生顺序。
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    device_id: Mapped[str | None] = mapped_column(String(64))

    # ── 测试模式（ADR-013）──
    #: true 表示这是测试模式的答题 —— 回放时跳过，不影响 Leitner 进度
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: 属于哪次测试。用于"测试记录"页把一批答题归为一次考试
    test_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_sessions.id", ondelete="CASCADE")
    )

    # ── 更正事件（ADR-013）──
    #: 非空表示这是一条"其实我会"的更正，指向被更正的那次答题。
    #: 回放时把被指向的事件视为答对；更正事件本身不参与状态转移。
    corrects_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("answer_events.id", ondelete="CASCADE")
    )

    # 入库时刻（服务器时间）。与 answered_at 偏差过大可用于检测客户端时钟异常（v2）。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def is_correction(self) -> bool:
        return self.corrects_event_id is not None

    def __repr__(self) -> str:
        if self.is_correction:
            return f"<AnswerEvent 更正 →{self.corrects_event_id}>"
        mark = "✓" if self.is_correct else "✗"
        flag = " [测试]" if self.is_test else ""
        return f"<AnswerEvent {mark} {self.mode.value}{flag} @{self.answered_at:%Y-%m-%d %H:%M}>"

