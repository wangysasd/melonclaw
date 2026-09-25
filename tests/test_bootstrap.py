"""演示用户初始化的租户归属约束。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from melonclaw.repository.bootstrap import seed_demo_data
from melonclaw.repository.errors import SeedDataConflictError


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
    assert seed["tenant_role"] == "member"
    assert seed["tenant_status"] == "active"
