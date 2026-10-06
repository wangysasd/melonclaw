"""应用保留历史数据的数据库增量更新。"""

from __future__ import annotations

import asyncio
import sys

from melonclaw.core.config import load_settings
from melonclaw.database import (
    Database,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
)
from melonclaw.database.update_runner import apply_schema_updates


async def update_database() -> tuple[str, ...]:
    database = Database(load_settings().database_url)
    try:
        await database.open()
        applied = await apply_schema_updates(database)
        await database.verify_schema(require_checkpointer=False, require_store=False)
        return applied
    finally:
        await database.close()


def main() -> None:
    try:
        applied = asyncio.run(update_database())
    except (
        DatabaseConfigurationError,
        DatabaseSchemaError,
        DatabaseUnavailableError,
        RuntimeError,
    ) as exc:
        print(f"数据库更新失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if applied:
        print(f"数据库更新完成：{', '.join(applied)}。现有数据已保留。")
    else:
        print("数据库已是最新状态，无需更新。")


if __name__ == "__main__":
    main()
