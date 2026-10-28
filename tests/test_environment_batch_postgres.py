"""多变量审批的原子更新、幂等、脱敏与缓存发布（隔离 schema）。"""

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_environment_api import environment_api

from melonclaw.services.environment import EnvironmentService
from melonclaw.services.environment_chat import ChatEnvironmentService, parse_environment_chat

pytestmark = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接")


@pytest.mark.parametrize("user_id", ["admin", "alice"])
def test_environment_batch_atomic_replacement_receipt_and_cache_generation(user_id):
    async def run():
        async with environment_api() as (_, storage):
            runtime = SimpleNamespace(require_ready=lambda: storage)
            management = EnvironmentService(runtime)
            provider = ChatEnvironmentService(SimpleNamespace(runtime=runtime))
            user = await storage.get_user_context(user_id)
            conversation = await storage.create_conversation(user_id, None)
            context = SimpleNamespace(user_id=user_id, tenant_id=user.tenant_id, project_id="",
                conversation_id=conversation["id"], user_message_id=str(uuid4()), request_id=str(uuid4()))

            async def stage(values):
                context.request_id = str(uuid4())
                clean, draft = parse_environment_chat("配置以下永久环境变量\n" + values,
                    user_id=user_id, conversation_id=context.conversation_id, request_id=context.request_id)
                assert "fixture" not in clean
                await provider.stage(draft, user, context.conversation_id, context.request_id)
                prepared = await provider.prepare(context, draft["id"])
                assert prepared["status"] == "prepared"
                assert "fixture" not in json.dumps(prepared)
                return draft, prepared["change"]

            # 已存在的值被替换，其他变量保留；两个值在同一回执中记录结果。
            await management.save(user_id, name="APP_ID", fields={"value": "fixture-old"})
            before = await storage.resource_versions.read("bob" if user_id == "admin" else user_id)
            draft, manifest = await stage("APP_ID=fixture-new\nAPP_SECRET=fixture-secret")
            assert [item["action"] for item in manifest["changes"]] == ["update", "create"]
            forged = {**manifest, "changes": [{**manifest["changes"][0], "name": "FORGED"}, manifest["changes"][1]]}
            assert (await provider.confirm(context, forged))["status"] == "error"
            results = await asyncio.gather(provider.confirm(context, manifest), provider.confirm(context, manifest))
            assert all(item["status"] == "saved" for item in results)
            assert results[0]["items"] == results[1]["items"]
            assert await storage.effective_environment(user_id) == {"APP_ID": "fixture-new", "APP_SECRET": "fixture-secret"}
            assert await storage.resource_versions.read("bob" if user_id == "admin" else user_id) != before
            operation = await storage.environment_operation(draft["id"], user_id, context.conversation_id)
            assert operation["payload"] is None and "fixture" not in json.dumps(operation["response"])

            # 第二项触发事务内 64KiB 配额检查：第一项已经执行的 SQL 也必须回滚。
            for index in range(5):
                await management.save(user_id, name=f"FILL_{index}", fields={"value": "x" * 8192})
            _, oversized = await stage("APP_ID=fixture-rollback\nNEW_A=" + "x" * 8192 + "\nNEW_B=" + "x" * 8192 + "\nNEW_C=" + "x" * 8192)
            assert (await provider.confirm(context, oversized))["status"] == "error"
            effective = await storage.effective_environment(user_id)
            assert effective["APP_ID"] == "fixture-new" and "NEW_A" not in effective
            # 权限变化/审批版本冲突不能部分提交。
            _, conflict = await stage("APP_ID=fixture-conflict\nAPP_SECRET=fixture-conflict-secret")
            row = next(row for row in await storage.list_environment(user_id) if row["name"] == "APP_SECRET")
            await management.save(user_id, identifier=row["id"], expected_version=row["version"], fields={"description": "changed"})
            assert (await provider.confirm(context, conflict))["status"] == "error"
            assert (await storage.effective_environment(user_id))["APP_ID"] == "fixture-new"

    asyncio.run(run())
