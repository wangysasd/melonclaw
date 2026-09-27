"""系统租户、admin、模型供应商与平台默认模型的种子初始化约束。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from melonclaw.repository.bootstrap import (
    seed_demo_data,
    seed_provider_data,
)
from melonclaw.repository.errors import SeedDataConflictError
from melonclaw.repository.seed_data import (
    ADMIN_USER_SEED,
    PROVIDER_SEEDS,
    TENANT_SEEDS,
)


class _RecordingConnection:
    """记录 execute 调用，供种子 SQL 断言。"""

    def __init__(self, statements):
        self._statements = statements

    async def execute(self, statement, values):
        self._statements.append((str(statement), values))
        return SimpleNamespace()


class _RecordingTransaction:
    def __init__(self, statements):
        self._statements = statements

    async def __aenter__(self):
        return _RecordingConnection(self._statements)

    async def __aexit__(self, exc_type, _exc, _traceback):
        return False


def _recording_database(statements):
    return SimpleNamespace(
        engine=SimpleNamespace(begin=lambda: _RecordingTransaction(statements))
    )


def test_seed_data_is_system_tenant_and_admin_only():
    assert TENANT_SEEDS == ({"tenant_id": "system", "tenant_name_zh": "系统"},)
    assert ADMIN_USER_SEED["user_id"] == "admin"
    assert ADMIN_USER_SEED["user_name_zh"] == "管理员"
    assert ADMIN_USER_SEED["tenant_role"] == "owner"


def test_seed_rejects_existing_user_in_another_tenant():
    statements: list[tuple[str, dict[str, str]]] = []

    class Connection:
        async def execute(self, statement, values):
            sql = str(statement)
            statements.append((sql, values))
            # PostgreSQL 的冲突更新 WHERE 不成立时，RETURNING 没有行。
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    class Transaction:
        def __init__(self):
            self.failed = False

        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, exc_type, _exc, _traceback):
            self.failed = exc_type is not None
            return False

    transaction = Transaction()
    database = SimpleNamespace(engine=SimpleNamespace(begin=lambda: transaction))

    with pytest.raises(SeedDataConflictError, match="租户归属与种子数据不一致"):
        asyncio.run(seed_demo_data(database))

    assert transaction.failed
    assert len(statements) == 2  # 租户批量写入后，首个冲突用户立即失败。
    user_insert, seed = statements[1]
    assert "WHERE users.tenant_id = EXCLUDED.tenant_id" in user_insert
    assert "RETURNING user_id" in user_insert
    assert "tenant_role" in user_insert
    assert "tenant_status" in user_insert
    assert seed["tenant_role"] == "owner"
    assert seed["tenant_status"] == "active"


def test_provider_seeds_reference_valid_builtins():
    assert len(PROVIDER_SEEDS) == 23
    keys = {seed["provider_key"] for seed in PROVIDER_SEEDS}
    assert {"deepseek", "siliconflow-cn", "openai", "alibaba-cn"} <= keys
    for seed in PROVIDER_SEEDS:
        assert seed["base_url"].startswith("https://")
        assert seed["models_endpoint"].startswith("https://")
        assert seed.get("provider_type") == "openai_compatible"
    # 纯 chat：不含 embedding/rerank 能力与多 base_url 字段。
    for seed in PROVIDER_SEEDS:
        assert "capabilities" not in seed
        assert "embedding_base_url" not in seed
        assert "rerank_base_url" not in seed
    enabled = {seed["provider_key"] for seed in PROVIDER_SEEDS if seed.get("enabled")}
    assert enabled == set()


def test_seed_provider_data_inserts_only_missing_rows(monkeypatch):
    statements: list[tuple[str, dict]] = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")

    asyncio.run(seed_provider_data(_recording_database(statements)))

    assert len(statements) == len(PROVIDER_SEEDS)
    for sql, values in statements:
        assert "INSERT INTO model_providers" in sql
        assert "ON CONFLICT (provider_key) DO NOTHING" in sql
        assert "'global', 'system'" in sql
    by_key = {values["provider_key"]: values for _sql, values in statements}
    assert by_key["deepseek"]["api_key"] is None
    assert all(not values["api_key_env"] and not values["enabled"] for _, values in statements)
    assert by_key["deepseek"]["created_by"] == "admin"

    # 未配置 Key 时种入 NULL，管理员之后在资源管理界面补录。
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    statements.clear()
    asyncio.run(seed_provider_data(_recording_database(statements)))
    by_key = {values["provider_key"]: values for _sql, values in statements}
    assert by_key["deepseek"]["api_key"] is None


def test_settings_do_not_load_environment_model(monkeypatch):
    from melonclaw.core.config import load_settings
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-unused-key")
    monkeypatch.setenv("DEEPSEEK_MODEL_FLASH", "unused-model")
    settings = load_settings()
    assert not hasattr(settings, "api_key")
    assert not hasattr(settings, "model_name")
