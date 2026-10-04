"""隔离 PostgreSQL Schema 验证完成事务、最新交付与索引重建。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import (
    chat_messages,
    conversation_artifacts,
    metadata,
    tenants,
    users,
)
from melonclaw.repository import AssistantStateConflictError, BusinessRepository
from melonclaw.repository.mappers import _now
from melonclaw.services.result_index import extract_result_refs


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离验证数据库连接")
def test_artifact_completion_is_atomic_latest_and_rebuildable(monkeypatch):
    async def run():
        schema = "test_artifacts_" + uuid4().hex
        engine = create_async_engine(normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
                                     connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.run_sync(metadata.create_all)
                await connection.execute(insert(tenants).values(tenant_id="test", tenant_name_zh="测试", created_at=_now()))
                await connection.execute(insert(users).values(user_id="owner", tenant_id="test", user_name_zh="用户", created_at=_now()))
            storage = BusinessRepository(SimpleNamespace(engine=engine))
            cid = UUID((await storage.create_conversation("owner", None))["id"])
            refs = [{"path": "/outputs/report.html"}]
            content = "[报告](/outputs/report.html)"
            now = _now()

            async def pending(seq):
                mid = uuid4()
                async with engine.begin() as connection:
                    await connection.execute(insert(chat_messages).values(
                        id=mid, conversation_id=cid, seq=seq, request_id=str(mid), role="assistant",
                        content="", status="pending", created_at=now, updated_at=now,
                    ))
                return mid

            async def complete(mid, body=content, selected=refs):
                return await storage.update_assistant(cid, mid, content=body, status="completed",
                                                      artifact_refs=selected, expected_status="pending")

            old, new = await pending(1), await pending(2)
            await complete(new)
            await complete(old)
            assert (await storage.list_artifacts(cid, "owner"))[0]["message_id"] == str(new)
            assert await storage.list_artifacts(cid, "other") == []

            with pytest.raises(AssistantStateConflictError):
                await complete(old, "[幽灵](/outputs/ghost.html)", [{"path": "/outputs/ghost.html"}])
            assert len(await storage.list_artifacts(cid, "owner")) == 1

            async def fail_index(*args):
                raise RuntimeError("模拟索引写入失败")

            failed = await pending(3)
            with monkeypatch.context() as patch:
                patch.setattr("melonclaw.repository.conversations.upsert_artifacts", fail_index)
                with pytest.raises(RuntimeError, match="模拟索引写入失败"):
                    await complete(failed)
            async with engine.connect() as connection:
                row = (await connection.execute(select(chat_messages).where(chat_messages.c.id == failed))).mappings().one()
                assert row["status"] == "pending" and row["content"] == ""
            assert (await storage.list_artifacts(cid, "owner"))[0]["message_id"] == str(new)

            # 长历史大多没有文件交付；查询仍只有真实交付项。
            async with engine.begin() as connection:
                for start in range(4, 20004, 1000):
                    await connection.execute(insert(chat_messages), [
                        {"id": uuid4(), "conversation_id": cid, "seq": seq, "request_id": str(uuid4()),
                         "role": "assistant", "content": "普通回答", "status": "completed", "created_at": now, "updated_at": now}
                        for seq in range(start, start + 1000)
                    ])
                await connection.execute(delete(conversation_artifacts))
            assert await storage.rebuild_artifacts(extract_result_refs) == 1
            assert (await storage.list_artifacts(cid, "owner"))[0]["message_id"] == str(new)
            assert await storage.rebuild_artifacts(extract_result_refs) == 1

            # 重建中解析失败，清空与部分重写也必须整体回滚。
            def fail_parse(_content):
                raise ValueError("模拟重建失败")
            with pytest.raises(ValueError, match="模拟重建失败"):
                await storage.rebuild_artifacts(fail_parse)
            assert (await storage.list_artifacts(cid, "owner"))[0]["message_id"] == str(new)
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()
    asyncio.run(run())
