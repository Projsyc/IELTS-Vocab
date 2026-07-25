"""密码哈希与 JWT。

═══════════════════════════════════════════════════════════════════════
为什么不用 passlib
═══════════════════════════════════════════════════════════════════════

原计划用 passlib（`requirements.txt` 里曾有 `passlib[bcrypt]`），实测不可用：

    AttributeError: module 'bcrypt' has no attribute '__about__'
    ValueError: password cannot be longer than 72 bytes

passlib 1.7.4 靠读 `bcrypt.__about__.__version__` 探测版本，
但 bcrypt 4.1+ 已移除该属性，之后的 fallback 路径也崩了。
passlib 最后一次发版是 2020 年，实际已停止维护。

所以直接用 `bcrypt` 库 —— API 只有两个函数，少一层不维护的依赖。
详见 docs/07-bug-log.md BUG-007。

═══════════════════════════════════════════════════════════════════════
关于 SHA-256 预哈希
═══════════════════════════════════════════════════════════════════════

bcrypt 有 **72 字节输入上限**（超了直接抛 ValueError），且遇到 NUL 字节会截断。

所以先用 SHA-256 摘要再 base64 编码，得到恒定 44 字节的输入：

    密码（任意长度） → SHA-256 → base64 → 44 字节 → bcrypt

这是业界常见做法，同时解决长度上限和 NUL 截断两个问题。

⚠️ **预哈希方式一旦上线就不能改** —— 改了所有已有密码都会失效。
   真要改，得让用户重设密码，或在 users 表加个 `hash_version` 字段做迁移。
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

__all__ = [
    "InvalidTokenError",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
]


class InvalidTokenError(Exception):
    """token 无效、过期或格式不对。

    自定义异常而不是直接透出 JWTError —— 让 routers 层能统一处理，
    也避免把 jose 的实现细节泄漏到上层。
    """


# ─────────────────────────────────────────────────────────────
# 密码
# ─────────────────────────────────────────────────────────────


def _prehash(password: str) -> bytes:
    """SHA-256 摘要 + base64 → 恒定 44 字节。

    绕过 bcrypt 的 72 字节上限与 NUL 字节截断问题。
    ⚠️ 改这个函数会让所有已存密码失效，见模块 docstring。
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    """生成密码哈希，返回可直接存进 `users.password_hash` 的字符串。

    每次调用产生不同的盐，所以同一密码两次哈希结果不同 —— 这是正确行为。
    """
    if not password:
        raise ValueError("密码不能为空")
    hashed = bcrypt.hashpw(_prehash(password), bcrypt.gensalt())
    return hashed.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码。任何异常都返回 False，不向调用方泄漏原因。

    哈希串损坏、格式不对、密码为空 —— 对调用方都只是"验证失败"。
    """
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # 哈希串不是合法 bcrypt 格式（数据被改坏 / 手工写错）
        return False


# ─────────────────────────────────────────────────────────────
# JWT
# ─────────────────────────────────────────────────────────────


def create_access_token(
    user_id: uuid.UUID | str,
    expires_delta: timedelta | None = None,
) -> str:
    """签发 access token。

    Args:
        user_id:        放进 `sub` 声明的用户 ID
        expires_delta:  自定义有效期，默认取配置里的 ACCESS_TOKEN_EXPIRE_MINUTES

    `iat`（签发时刻）也一起写进去，便于将来做"某时刻之前签发的 token 全部失效"。
    """
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    """校验 token 并取出用户 ID。

    Raises:
        InvalidTokenError: 签名不对、已过期、缺 `sub`、或 `sub` 不是合法 UUID。

    过期与签名错误**都抛同一个异常** —— 不向客户端区分原因，避免给攻击者线索。
    """
    if not token:
        raise InvalidTokenError("token 为空")

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError as exc:
        raise InvalidTokenError("token 无效或已过期") from exc

    sub = payload.get("sub")
    if not sub:
        raise InvalidTokenError("token 缺少 sub 声明")

    try:
        return uuid.UUID(str(sub))
    except ValueError as exc:
        raise InvalidTokenError("token 里的 sub 不是合法 UUID") from exc
