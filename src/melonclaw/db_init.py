"""独立初始化 MelonClaw 业务表和 LangGraph PostgreSQL Checkpointer。"""

from __future__ import annotations

import asyncio
import sys

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from melonclaw.core.config import load_settings
from melonclaw.core.database import (
    BusinessDatabase,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
    close_memory_store,
    open_checkpoint_pool,
    open_memory_store,
)


async def initialize_database() -> None:
    settings = load_settings()
    database = BusinessDatabase(settings.database_url)
    checkpoint_pool = None
    memory_store_context = None
    try:
        await database.open()
        await database.create_schema()
        memory_store_context, memory_store = await open_memory_store(
            settings.database_url
        )
        await memory_store.setup()
        checkpoint_pool = await open_checkpoint_pool(settings.database_url)
        checkpointer = AsyncPostgresSaver(checkpoint_pool)
        await checkpointer.setup()
        await database.verify_schema(
            require_checkpointer=True,
            require_store=True,
        )
    finally:
        if checkpoint_pool is not None:
            await checkpoint_pool.close()
        if memory_store_context is not None:
            await close_memory_store(memory_store_context)
        await database.close()


def main() -> None:
    try:
        asyncio.run(initialize_database())
    except (
        DatabaseConfigurationError,
        DatabaseSchemaError,
        DatabaseUnavailableError,
    ) as exc:
        print(f"数据库初始化失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        "MelonClaw 数据库初始化完成：业务表、PostgreSQL Checkpointer "
        "和 Memory Store 已就绪。"
    )


if __name__ == "__main__":
    main()
