"""pytest 共享 fixture。

关键点：async engine 的连接池绑定在**创建它的事件循环**上。
pytest-asyncio 默认每个测试一个新循环，所以 engine 也必须每个测试新建并销毁，
否则第二个测试会报 "Event loop is closed"（见 docs/07-bug-log.md BUG-005）。
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


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
