"""Alembic 迁移环境配置。

与默认模板的差异：
  1. 从 app.core.config 读 DATABASE_URL，不写死在 alembic.ini（避免密码进 git）
  2. target_metadata 指向 app.core.database.Base.metadata
  3. 开启 compare_type / compare_server_default，让 autogenerate 能检测字段类型变化
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ⚠️ 必须 import app.models，否则 Base.metadata 是空的，
#    autogenerate 会生成"删除所有表"的迁移
import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base

config = context.config

# 用应用配置里的连接串覆盖 alembic.ini 中的占位值
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库。"""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,             # 检测字段类型变化
        compare_server_default=True,   # 检测默认值变化
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
