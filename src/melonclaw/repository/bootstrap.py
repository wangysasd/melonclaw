"""演示环境业务数据的幂等初始化。"""

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy import text

from melonclaw.core.mcp_config import load_builtin_mcp_seed
from melonclaw.database.database import Database
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID
from melonclaw.repository.errors import SeedDataConflictError
from melonclaw.repository.mappers import _now
from melonclaw.repository.seed_data import (
    PROVIDER_SEEDS,
    TENANT_SEEDS,
    USER_SEEDS,
)

DEFAULT_TUSHARE_SERVER_NAME = "tushare_mcp"


async def seed_demo_data(database: Database) -> None:
    """幂等写入演示租户和各自唯一归属的用户。"""

    async with database.engine.begin() as connection:
        timestamp = _now()
        await connection.execute(
            text(
                "INSERT INTO tenants "
                "(tenant_id, tenant_name_zh, created_at) "
                "VALUES (:tenant_id, :tenant_name_zh, :created_at) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "tenant_name_zh = EXCLUDED.tenant_name_zh"
            ),
            [{**item, "created_at": timestamp} for item in TENANT_SEEDS],
        )
        for item in USER_SEEDS:
            # PostgreSQL 在冲突行不满足 UPDATE WHERE 时不会更新，也不会把该行放进 RETURNING。
            # 因此无返回行表示既有用户已归属另一租户，不能在重跑种子时静默改归属。
            result = await connection.execute(
                text(
                    "INSERT INTO users "
                    "(user_id, tenant_id, user_name_zh, tenant_role, tenant_status, created_at) "
                    "VALUES "
                    "(:user_id, :tenant_id, :user_name_zh, :tenant_role, :tenant_status, :created_at) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "user_name_zh = EXCLUDED.user_name_zh, "
                    "tenant_role = EXCLUDED.tenant_role "
                    "WHERE users.tenant_id = EXCLUDED.tenant_id "
                    "RETURNING user_id"
                ),
                {**item, "created_at": timestamp},
            )
            if result.scalar_one_or_none() is None:
                raise SeedDataConflictError(str(item["user_id"]))


async def seed_builtin_data(database: Database) -> None:
    """把仓库内置 mcp.json 单向同步进数据库。

    同步只在“数据库缺行”时补录，绝不覆盖管理员在 UI 上改过的启用状态、
    白名单等运营字段；既有行的内容更新走资源管理 API。
    内置 Skill 没有种子：系统级 Skill 的唯一存储是 data_root/skills/shared/，
    索引由 ``services/skill_index.py`` 在 db-init 时从磁盘重建。
    """

    timestamp = _now()
    async with database.engine.begin() as connection:
        tushare_allowlist = _tushare_allowlist_from_env()
        for slug, config in sorted(load_builtin_mcp_seed().items()):
            await connection.execute(
                text(
                    "INSERT INTO mcp_servers "
                    "(id, slug, scope, source_type, transport, url, command, args, "
                    "env, headers, tool_allowlist, enabled, created_by, "
                    "created_at, updated_at) "
                    "VALUES "
                    "(gen_random_uuid(), :slug, 'global', 'builtin', :transport, "
                    ":url, :command, :args, :env, :headers, :tool_allowlist, "
                    "true, :created_by, :created_at, :updated_at) "
                    "ON CONFLICT (slug) DO NOTHING"
                ),
                _builtin_mcp_params(
                    slug,
                    config,
                    tushare_allowlist if slug == DEFAULT_TUSHARE_SERVER_NAME else None,
                    timestamp,
                ),
            )


async def seed_provider_data(database: Database) -> None:
    """把平台默认模型供应商单向补录进 model_providers。

    只在“数据库缺行”时插入，绝不覆盖管理员在 UI 上改过的名称、Key、
    启用状态等运营字段；既有行的内容更新走资源管理 API。
    所有模板初始停用且没有凭据，登录后在 UI 配置。
    """

    timestamp = _now()
    async with database.engine.begin() as connection:
        for seed in PROVIDER_SEEDS:
            await connection.execute(
                text(
                    "INSERT INTO model_providers "
                    "(id, provider_key, scope, source_type, display_name, "
                    "provider_type, base_url, api_key, api_key_env, models_endpoint, "
                    "enabled, created_by, version, created_at, updated_at) "
                    "VALUES "
                    "(gen_random_uuid(), :provider_key, 'global', 'system', "
                    ":display_name, :provider_type, :base_url, :api_key, :api_key_env, "
                    ":models_endpoint, :enabled, :created_by, 1, :created_at, "
                    ":updated_at) "
                    "ON CONFLICT (provider_key) DO NOTHING"
                ),
                {
                    "provider_key": seed["provider_key"],
                    "display_name": seed["display_name"],
                    "provider_type": seed.get("provider_type") or "openai_compatible",
                    "base_url": seed["base_url"],
                    "api_key": None,
                    "api_key_env": "",
                    "models_endpoint": seed.get("models_endpoint"),
                    "enabled": False,
                    "created_by": DEFAULT_SIMULATED_USER_ID,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )


def _tushare_allowlist_from_env() -> list[str] | None:
    raw = os.getenv("DEEPAGENTS_TUSHARE_MCP_TOOLS", "").strip()
    if not raw or raw == "*":
        return None
    return [name.strip() for name in raw.split(",") if name.strip()]


def _builtin_mcp_params(
    slug: str,
    config: dict[str, Any],
    tool_allowlist: list[str] | None,
    timestamp: Any,
) -> dict[str, Any]:
    args = config.get("args")
    return {
        "slug": slug,
        "transport": config.get("transport", "http"),
        "url": config.get("url"),
        "command": config.get("command"),
        "args": json.dumps(list(args)) if isinstance(args, list) else None,
        "env": json.dumps(
            {str(k): str(v) for k, v in (config.get("env") or {}).items()}
        ),
        "headers": json.dumps(
            {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
        ),
        "tool_allowlist": json.dumps(tool_allowlist) if tool_allowlist else None,
        "created_by": DEFAULT_SIMULATED_USER_ID,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
