"""认证接口的 HTTP 层测试。

用 httpx.ASGITransport 直接打 FastAPI app，不起真实服务器。
需要数据库在跑（docker compose up -d）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.security import create_access_token, hash_password
from app.models import User

# `client` fixture 定义在 conftest.py —— 它会覆盖 get_db 依赖，
# 否则路由会用模块级 engine，在第二个测试里报 "Event loop is closed"。


@pytest_asyncio.fixture
async def test_user(db_session) -> AsyncGenerator[tuple[User, str], None]:
    """建一个临时用户，返回 (用户, 明文密码)。用完删掉。"""
    password = "test-password-123"
    user = User(
        username=f"_http_{uuid.uuid4().hex[:8]}",
        nickname="接口测试",
        password_hash=hash_password(password),
    )
    db_session.add(user)
    await db_session.commit()

    yield user, password

    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


# ─────────────────────────────────────────────────────────────
# 健康检查
# ─────────────────────────────────────────────────────────────

async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ─────────────────────────────────────────────────────────────
# 登录
# ─────────────────────────────────────────────────────────────

async def test_login_success(client, test_user):
    user, password = test_user
    r = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["tokenType"] == "bearer"
    assert body["accessToken"]
    assert body["user"]["username"] == user.username
    assert body["user"]["nickname"] == "接口测试"
    assert body["user"]["dailyNewLimit"] == 20


async def test_login_response_uses_camel_case(client, test_user):
    """对外字段必须是 camelCase，与 packages/shared 的 TS 类型一致。"""
    user, password = test_user
    r = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    body = r.json()
    assert "accessToken" in body and "access_token" not in body
    assert "dailyNewLimit" in body["user"] and "daily_new_limit" not in body["user"]


async def test_login_never_leaks_password_hash(client, test_user):
    """⭐ 响应里绝不能出现密码哈希或微信字段。"""
    user, password = test_user
    r = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    raw = r.text
    assert "passwordHash" not in raw
    assert "password_hash" not in raw
    assert user.password_hash not in raw
    assert "wxOpenid" not in raw and "wx_openid" not in raw


async def test_login_wrong_password(client, test_user):
    user, _ = test_user
    r = await client.post(
        "/api/auth/login", json={"username": user.username, "password": "wrong"}
    )
    assert r.status_code == 401


async def test_login_unknown_username(client):
    r = await client.post(
        "/api/auth/login", json={"username": "_no_such_user_", "password": "whatever"}
    )
    assert r.status_code == 401


async def test_login_same_message_for_unknown_user_and_wrong_password(client, test_user):
    """⭐ 两种失败必须返回**完全相同**的响应。

    否则攻击者能靠错误信息差异枚举出哪些用户名是有效的。
    """
    user, _ = test_user
    wrong_pw = await client.post(
        "/api/auth/login", json={"username": user.username, "password": "wrong"}
    )
    no_user = await client.post(
        "/api/auth/login", json={"username": "_no_such_user_", "password": "wrong"}
    )
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()


@pytest.mark.parametrize("payload", [
    {},
    {"username": "alice"},
    {"password": "pw"},
    {"username": "", "password": "pw"},
    {"username": "alice", "password": ""},
])
async def test_login_validation_errors(client, payload):
    r = await client.post("/api/auth/login", json=payload)
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────
# /me
# ─────────────────────────────────────────────────────────────

async def test_me_with_valid_token(client, test_user):
    user, password = test_user
    login = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    token = login.json()["accessToken"]

    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == user.username


async def test_me_without_token(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


@pytest.mark.parametrize("header", [
    "Bearer garbage",
    "Bearer ",
    "Basic dXNlcjpwYXNz",
    "garbage",
])
async def test_me_with_bad_auth_header(client, header):
    r = await client.get("/api/auth/me", headers={"Authorization": header})
    assert r.status_code == 401


async def test_me_with_expired_token(client, test_user):
    user, _ = test_user
    expired = create_access_token(user.id, expires_delta=timedelta(seconds=-10))
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


async def test_me_with_token_for_deleted_user(client):
    """⭐ token 签名有效但用户已被删除 → 也是 401，不能 500。"""
    ghost = create_access_token(uuid.uuid4())
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {ghost}"})
    assert r.status_code == 401


async def test_me_response_has_www_authenticate_header(client):
    r = await client.get("/api/auth/me")
    assert r.headers.get("WWW-Authenticate") == "Bearer"


async def test_me_never_leaks_password_hash(client, test_user):
    user, password = test_user
    login = await client.post(
        "/api/auth/login", json={"username": user.username, "password": password}
    )
    token = login.json()["accessToken"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert "passwordHash" not in r.text
    assert user.password_hash not in r.text


# ─────────────────────────────────────────────────────────────
# OpenAPI 文档
# ─────────────────────────────────────────────────────────────

async def test_openapi_schema_generates(client):
    """能生成 OpenAPI schema —— 将来可用它自动生成前端类型。"""
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/api/auth/login" in paths
    assert "/api/auth/me" in paths
