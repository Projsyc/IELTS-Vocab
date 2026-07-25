"""用户表。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str] = mapped_column(String(50), nullable=False)

    # 预留：微信小程序一键登录（v3 启用，见 ADR-006）
    wx_openid: Mapped[str | None] = mapped_column(String(64), unique=True)
    wx_unionid: Mapped[str | None] = mapped_column(String(64), unique=True)

    # 每日任务配额，存用户级别以便不同人不同强度
    daily_new_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    daily_review_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<User {self.username}>"
