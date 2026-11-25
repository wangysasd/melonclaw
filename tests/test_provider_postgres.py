"""同一供应商的协议切换和模型写入使用 PostgreSQL 目录锁。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.core.model_catalog import ModelConfigError
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import metadata
from melonclaw.repository import BusinessRepository
from melonclaw.repository.bootstrap import seed_demo_data


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接")
def test_provider_protocol_changes_are_atomic():
    async def run():
        url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
        schema = f"test_provider_{uuid4().hex}"
        control = create_async_engine(url)
        async with control.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": f"{schema},public"}})
        try:
            async with engine.begin() as conn:
                await conn.run_sync(metadata.create_all)
            database = SimpleNamespace(engine=engine)
            await seed_demo_data(database)
            # 独立仓储实例使数据库事务锁成为并发写入的共同边界。
            first, second = BusinessRepository(database), BusinessRepository(database)
            await first.create_provider_row(
                provider_key="protocol-test", scope="global", source_type="manual",
                display_name="Protocol test", provider_type="openai_compatible",
                base_url="https://example.invalid", api_key=None, models_endpoint=None, created_by="admin",
            )

            def create_parser():
                return second.create_model_row(
                    provider_key="protocol-test", model_key="parser", scope="global", source_type="manual",
                    display_name="Parser", model_name="test-parser", purpose="document_parser",
                    context_window=None, created_by="admin",
                )

            def switch():
                return first.update_provider_row("protocol-test", provider_type="anthropic")

            for conflicting_write in (
                create_parser,
                lambda: second.update_provider_row("protocol-test", extra_config={"enable_thinking": True}),
                lambda: second.update_provider_row("protocol-test", request_headers={"X-Api-KEY": "placeholder"}),
            ):
                results = await asyncio.wait_for(
                    asyncio.gather(switch(), conflicting_write(), return_exceptions=True), timeout=10,
                )
                failures = [result for result in results if isinstance(result, Exception)]
                assert len(failures) == 1
                assert isinstance(failures[0], ModelConfigError)
                row = await first.get_provider_row("protocol-test")
                if row["provider_type"] == "anthropic":
                    assert not await first.list_models_of_provider("protocol-test")
                    assert row["extra_config"] == {}
                    assert row["request_headers"] == {}
                else:
                    assert row["provider_type"] == "openai_compatible"
                if await first.get_model_row("parser"):
                    await first.delete_model_row("parser")
                await first.update_provider_row(
                    "protocol-test", provider_type="openai_compatible", extra_config={}, request_headers={},
                )
        finally:
            await engine.dispose()
            async with control.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())
