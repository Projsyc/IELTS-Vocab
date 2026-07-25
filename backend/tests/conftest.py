"""pytest 共享 fixture。

═══════════════════════════════════════════════════════════════════════
⚠️ 关于 async engine 与事件循环（这个坑踩了三次）
═══════════════════════════════════════════════════════════════════════

**async engine 的连接池绑定在创建它的事件循环上。**
pytest-asyncio 默认每个测试一个新循环，所以任何跨测试复用的 engine
都会在第二个测试里报 "Event loop is closed"。

已经踩到三次：
    BUG-005  测试里直接用模块级 engine
    BUG-006  脚本收尾用第二个 asyncio.run() 去 dispose
    本文件   HTTP 测试打 main.app，而 app 里的路由用模块级 engine

对应的三条规则：
    1. 测试里的 engine 必须是 fixture，每个测试新建并 dispose
    2. dispose 必须 await 在建立连接的那个循环里
    3. **HTTP 测试必须覆盖 get_db 依赖**，否则 app 会用模块级 engine
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import main
from app.core.config import settings
from app.core.database import get_db


@pytest_asyncio.fixture
async def db_conn() -> AsyncGenerator[AsyncConnection, None]:
    """裸连接，用于查 information_schema / pg_catalog 这类结构性断言。"""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=None)
    async with engine.connect() as conn:
        yield conn
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """ORM session，用于 CRUD 测试。"""
    engine = create_async_engine(settings.DATABASE_URL)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """打 FastAPI app 的 HTTP 客户端（不起真实服务器）。

    ⚠️ 必须覆盖 `get_db`：否则路由会用 `app.core.database` 里的模块级 engine，
       那个 engine 绑在第一个测试的事件循环上，第二个测试就炸。

    顺带的好处：app 和测试共用一个 session，测试里创建的数据
    对被测接口立即可见，不用操心事务隔离。
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    main.app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://test"
        ) as c:
            yield c
    finally:
        main.app.dependency_overrides.clear()
