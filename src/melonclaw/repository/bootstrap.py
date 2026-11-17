"""演示环境业务数据的幂等初始化。"""

from __future__ import annotations

from sqlalchemy import text

from melonclaw.core.passwords import hash_password
from melonclaw.database.database import Database
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID
from melonclaw.repository.mappers import _now
from melonclaw.repository.seed_data import (
    PROVIDER_SEEDS,
    TENANT_SEEDS,
    USER_SEEDS,
)


async def seed_demo_data(database: Database) -> None:
    """幂等写入演示租户和各自唯一归属的用户。"""

    async with database.engine.begin() as connection:
        timestamp = _now()
        await connection.execute(
            text(
                "INSERT INTO tenants "
                "(tenant_id, tenant_name_zh, created_at) "
                "VALUES (:tenant_id, :tenant_name_zh, :created_at) "
                "ON CONFLICT (tenant_id) DO NOTHING"
            ),
            [{**item, "created_at": timestamp} for item in TENANT_SEEDS],
        )
        for item in USER_SEEDS:
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(user_id, tenant_id, user_name_zh, tenant_role, tenant_status, password_hash, created_at) "
                    "VALUES (:user_id, :tenant_id, :user_name_zh, :tenant_role, :tenant_status, :password_hash, :created_at) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {**item, "password_hash": hash_password("admin"), "created_at": timestamp},
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
                    "provider_type": seed["provider_type"],
                    "base_url": seed["base_url"],
                    "api_key": None,
                    "api_key_env": "",
                    "models_endpoint": seed["models_endpoint"],
                    "enabled": False,
                    "created_by": DEFAULT_SIMULATED_USER_ID,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
            )
