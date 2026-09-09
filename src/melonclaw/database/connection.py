"""业务数据库、Checkpointer 和 Memory Store 的连接管理。"""

from __future__ import annotations

from typing import Any

from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url

from melonclaw.database.errors import (
    DatabaseConfigurationError,
    DatabaseUnavailableError,
)

def normalize_async_database_url(raw_url: str) -> str:
    """校验业务连接使用 asyncpg，并保留 URL 中的其余配置。"""

    if not raw_url.strip():
        raise DatabaseConfigurationError("未配置 DATABASE_URL。")
    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise DatabaseConfigurationError("DATABASE_URL 格式无效。") from exc
    if url.drivername != "postgresql+asyncpg":
        raise DatabaseConfigurationError(
            "DATABASE_URL 必须使用 postgresql+asyncpg 驱动。"
        )
    return url.render_as_string(hide_password=False)


def derive_psycopg_database_url(raw_url: str) -> str:
    """从同一 DATABASE_URL 派生 psycopg URL，不重复维护数据库地址。"""

    async_url = normalize_async_database_url(raw_url)
    url = make_url(async_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


async def open_checkpoint_pool(raw_url: str) -> AsyncConnectionPool:
    """打开供 AsyncPostgresSaver 使用的 psycopg 异步连接池。"""

    connection_url = derive_psycopg_database_url(raw_url)
    pool = AsyncConnectionPool(
        connection_url,
        min_size=1,
        max_size=8,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        name="melonclaw-checkpointer",
    )
    try:
        await pool.open(wait=True)
    except Exception as exc:
        await pool.close()
        raise DatabaseUnavailableError(
            "PostgreSQL Checkpointer 连接失败，请检查 DATABASE_URL 和 PostgreSQL 认证配置。"
        ) from exc
    return pool


async def open_memory_store(
    raw_url: str,
) -> tuple[Any, AsyncPostgresStore]:
    """打开长期 Memory 使用的异步 PostgreSQL Store。"""

    connection_url = derive_psycopg_database_url(raw_url)
    context_manager = AsyncPostgresStore.from_conn_string(
        connection_url,
        pool_config={"min_size": 1, "max_size": 8},
    )
    try:
        store = await context_manager.__aenter__()
    except Exception as exc:
        raise DatabaseUnavailableError(
            "PostgreSQL Memory Store 连接失败，请检查 DATABASE_URL 和 PostgreSQL 配置。"
        ) from exc
    return context_manager, store


async def close_memory_store(context_manager: Any) -> None:
    """关闭由 ``open_memory_store`` 创建的异步 Store 和连接池。"""

    if context_manager is not None:
        await context_manager.__aexit__(None, None, None)

