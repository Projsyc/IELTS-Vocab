"""FastAPI 依赖注入 —— 当前登录用户。

放在 core/ 而非 routers/ 是因为所有需要鉴权的路由都要用它。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import InvalidTokenError, decode_access_token
from app.models import User

# auto_error=False：自己控制"缺 header"的错误信息，
# 顺便让 401 的响应体格式与其他鉴权失败一致
_bearer = HTTPBearer(auto_error=False, description="Bearer <access_token>")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或凭证已失效",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """从 Authorization 头解析出当前用户。

    所有失败路径（缺 header / token 无效 / 已过期 / 用户不存在）
    **都返回同一个 401**，不向客户端区分原因 —— 避免给攻击者线索，
    比如"用户不存在"和"密码错误"分开报会泄漏哪些用户名有效。
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED

    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidTokenError as exc:
        raise _UNAUTHORIZED from exc

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        # token 签名有效但用户已被删除 —— 同样只报 401
        raise _UNAUTHORIZED

    return user


#: 路由签名里直接用这个别名，省得每次写 Depends
CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
