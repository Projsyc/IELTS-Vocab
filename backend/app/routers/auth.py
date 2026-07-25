"""认证路由 —— 对应 docs/04-api-design.md §2。

按项目约定，本层只做"收参数 → 调 service/查库 → 返响应"，不写业务逻辑。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.security import create_access_token, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="用户名密码登录",
)
async def login(payload: LoginRequest, db: DbSession) -> LoginResponse:
    """登录并签发 access token。

    ⚠️ 用户名不存在与密码错误**返回同一个 401**，
       否则攻击者能靠错误信息枚举出哪些用户名是有效的。
    """
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    # 即便用户不存在也走一次密码校验，让两条路径耗时接近，
    # 减少通过响应时间差异枚举用户名的可能。
    stored_hash = user.password_hash if user else ""
    password_ok = verify_password(payload.password, stored_hash)

    if user is None or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return LoginResponse(
        access_token=create_access_token(user.id),
        user=UserOut.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserOut,
    summary="获取当前登录用户",
)
async def read_me(current_user: CurrentUser) -> UserOut:
    return UserOut.model_validate(current_user)
