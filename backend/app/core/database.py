"""数据库连接与 Session 管理（SQLAlchemy 2.x async）。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。Alembic 通过 Base.metadata 发现表结构。"""


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,  # 连接失效时自动重连，避免长时间空闲后报错
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # commit 后仍能读对象属性，省掉一次刷新查询
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入用。请求结束自动关闭 session。"""
    async with AsyncSessionLocal() as session:
        yield session
