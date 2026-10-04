"""独立初始化 MelonClaw 业务表和 LangGraph PostgreSQL Checkpointer。"""

from __future__ import annotations

import asyncio
import sys

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from melonclaw.core.config import load_settings
from melonclaw.database import (
    Database,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
    close_memory_store,
    open_checkpoint_pool,
    open_memory_store,
)
from melonclaw.repository import (
    BusinessRepository,
    seed_builtin_data,
    seed_demo_data,
    seed_provider_data,
)
from melonclaw.repository.errors import SeedDataConflictError
from melonclaw.services.result_index import extract_result_refs
from melonclaw.services.skill_index import reindex_skills_from_disk


async def initialize_database() -> None:
    settings = load_settings()
    database = Database(settings.database_url)
    checkpoint_pool = None
    memory_store_context = None
    try:
        await database.open()
        await database.create_schema()
        await seed_demo_data(database)
        await seed_provider_data(database)
        await seed_builtin_data(database)
        artifacts = await BusinessRepository(database).rebuild_artifacts(extract_result_refs)
        print(f"会话产物索引重建完成：{artifacts} 项交付。")
        # 索引重建必须排在种子用户之后：用户 Skill 行的 created_by 有外键约束。
        report = await reindex_skills_from_disk(
            BusinessRepository(database), settings.data_root
        )
        if report.changed:
            print(report.summary())
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
        SeedDataConflictError,
    ) as exc:
        print(f"数据库初始化失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        "MelonClaw 数据库初始化完成：业务表、PostgreSQL Checkpointer "
        "和 Memory Store 已就绪。"
    )


if __name__ == "__main__":
    main()
