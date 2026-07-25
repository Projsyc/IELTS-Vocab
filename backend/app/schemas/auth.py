"""认证相关的请求/响应模型。

对应 docs/04-api-design.md §2。

命名约定：字段用 snake_case 定义，通过 `alias_generator` 对外输出 camelCase，
与 `packages/shared/src/index.ts` 里的 TS 类型保持一致。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """所有对外模型的基类 —— snake_case 定义、camelCase 输出。

    populate_by_name=True 让两种写法都能作为输入，前端传哪种都行。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,   # 允许直接从 ORM 对象构造
    )


class LoginRequest(CamelModel):
    username: str = Field(min_length=1, max_length=50)
    # 上限 1024 而非 72 —— 密码经 SHA-256 预哈希后长度恒定，
    # 不受 bcrypt 的 72 字节限制（见 core/security.py）
    password: str = Field(min_length=1, max_length=1024)


class UserOut(CamelModel):
    """对外暴露的用户信息。**刻意不含 password_hash 和微信字段。**"""

    id: uuid.UUID
    username: str
    nickname: str
    daily_new_limit: int
    daily_review_limit: int
    created_at: datetime


class LoginResponse(CamelModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
