"""真实 PostgreSQL：MCP 更新 CAS、审批快照、幂等回执、权限和缓存失效。"""

import asyncio
import json
import os
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_environment_api import environment_api
from test_mcp_management import payload

from melonclaw.core.mcp_credentials import decode_credentials
from melonclaw.database.schema import mcp_install_drafts
from melonclaw.services.mcp_chat_config import parse_chat_mcp
from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator
from melonclaw.services.mcp_install import ChatMcpInstallService

pytestmark = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离验证数据库连接")


async def setup_provider(storage, user_id):
    conversation = await storage.create_conversation(user_id, None)
    user = await storage.get_user_context(user_id)
    context = SimpleNamespace(user_id=user_id, tenant_id=user.tenant_id, conversation_id=conversation["id"], project_id="")
    chat = SimpleNamespace(runtime=SimpleNamespace(require_ready=lambda: storage, mcp_discovery=McpDiscoveryCoordinator()),
                           conversations=SimpleNamespace(resolve_user=storage.get_user_context))
    return ChatMcpInstallService(chat), context


async def stage(storage, context, config):
    clean, sources = parse_chat_mcp("请更新下面的 MCP\n" + json.dumps({"mcpServers": {"demo": config}}),
        user_id=context.user_id, conversation_id=context.conversation_id, request_id=str(uuid4()))
    assert "fixture" not in clean
    await storage.stage_mcp_drafts(context.user_id, context.conversation_id, sources)
    return sources[0]["id"]


@pytest.mark.parametrize("user_id", ["admin", "alice"])
def test_mcp_update_keeps_permissions_and_is_atomic_idempotent_and_invalidates_cache(user_id):
    async def run():
        async with environment_api() as (_, storage):
            provider, context = await setup_provider(storage, user_id)
            created = await provider.management.create(user_id, {
                **payload(headers={"set": {"Authorization": "Bearer fixture-old", "X-Client": "fixture-client"}}),
                "transport": "sse", "description": "保留说明", "tool_allowlist": ["search"],
            })
            identifier = created["id"]
            if user_id == "admin":
                await provider.management.global_state(user_id, identifier, 1, True)
            await storage.set_mcp_preference(user_id, "demo", False)
            if user_id == "admin":
                await provider.management.global_state(user_id, identifier, 2, False)
            original = await storage.get_mcp_row(identifier)
            before = await storage.resource_versions.read("bob" if user_id == "admin" else user_id)
            source = await stage(storage, context, {"url": "https://example.test/mcp?api_key=fixture-new"})
            preview = await provider.prepare(context, source)
            manifest = preview["installation"]
            assert manifest["operation"] == "update" and manifest["changed_fields"] == ["url"]
            assert manifest["enable"] is False and manifest["tool_allowlist"] == ["search"]
            assert "fixture" not in json.dumps(preview)
            replies = await asyncio.gather(provider.confirm(context, manifest), provider.confirm(context, manifest))
            assert all(result["status"] == "updated" for result in replies)
            assert replies[0] == replies[1] and replies[0]["connection_status"] == "untested"
            assert "尚未验证连接" in replies[0]["message"]
            updated = await storage.get_mcp_row(identifier)
            assert updated["version"] == original["version"] + 1
            assert updated["url"].endswith("fixture-new")
            for key in ("id", "created_by", "created_at", "headers", "tool_allowlist", "transport", "enabled", "description", "display_name"):
                assert updated[key] == original[key]
            rows = await storage.list_visible_mcp_rows(user_id)
            assert len(rows) == 1 and rows[0]["user_enabled"] is False
            assert await storage.resource_versions.read("bob" if user_id == "admin" else user_id) != before
            operation = await storage.get_mcp_install_draft(manifest["draft_id"], user_id, context.conversation_id)
            assert set(operation["payload"]) == {"result"} and "fixture" not in str(operation["payload"])
            assert (await provider.prepare(context, source))["status"] == "unchanged"
            # 服务层已合并，但审批等待期间其他入口改版本：提交不能覆盖。
            source = await stage(storage, context, {"url": "https://example.test/next?api_key=fixture-next"})
            pending = (await provider.prepare(context, source))["installation"]
            await provider.management.update(user_id, identifier, updated["version"], {"description": "页面新修改"})
            provider.management._discover_tools = AsyncMock()
            assert (await provider.test(context, pending))["status"] == "error"
            assert (await provider.confirm(context, pending))["status"] == "error"
            provider.management._discover_tools.assert_not_awaited()
            assert (await storage.get_mcp_row(identifier))["url"] == updated["url"]
            unfinished = await storage.get_mcp_install_draft(pending["draft_id"], user_id, context.conversation_id)
            assert unfinished["installed_id"] is None and "row" in unfinished["payload"]
            # 事务内重新比较真实快照，不允许调用方替换批准的 URL。
            source = await stage(storage, context, {"url": "https://example.test/approved?key=fixture-approved"})
            pending = (await provider.prepare(context, source))["installation"]
            draft = await storage.get_mcp_install_draft(pending["draft_id"], user_id, context.conversation_id)
            forged = {**draft["payload"]["row"], "url": "https://evil.test/"}
            from melonclaw.repository.mcp import McpConflictError
            with pytest.raises(McpConflictError):
                await storage.commit_mcp_install(pending["draft_id"], user_id, context.conversation_id, pending, forged)
            assert (await storage.get_mcp_row(identifier))["url"] == updated["url"]

    asyncio.run(run())


def test_mcp_test_status_is_bound_to_snapshot_and_does_not_publish_tools_or_config():
    async def run():
        async with environment_api() as (_, storage):
            provider, context = await setup_provider(storage, "alice")
            source = await stage(storage, context, {"url": "https://example.test/mcp?api_key=fixture-one"})
            preview = await provider.prepare(context, source, tool_allowlist=["search"])
            manifest = preview["installation"]
            provider.management._discover_tools = AsyncMock(return_value={"ok": True, "tool_names": ["search", "new-tool"]})
            assert (await provider.test(context, manifest))["connection_status"] == "verified"
            assert await storage.list_visible_mcp_rows("alice") == []
            saved = await provider.confirm(context, manifest)
            assert saved["connection_status"] == "verified" and saved["status"] == "installed"
            row = await storage.get_mcp_row(saved["id"])
            assert row["tool_allowlist"] == ["search"]
            source = await stage(storage, context, {"url": "https://example.test/mcp?api_key=fixture-two"})
            pending = (await provider.prepare(context, source))["installation"]
            assert pending["tool_allowlist"] == ["search"]
            provider.management._discover_tools.return_value = {"ok": False, "tool_names": [], "message": "鉴权失败"}
            assert (await provider.test(context, pending))["connection_status"] == "failed"
            assert (await storage.get_mcp_row(saved["id"]))["url"] == row["url"]
            saved_failed = await provider.confirm(context, pending)
            assert saved_failed["connection_status"] == "failed" and "测试失败" in saved_failed["message"]
            assert saved_failed["version"] == 2
            assert await provider.confirm(context, pending) == saved_failed
            # 已提交后到达的测试结果不能复活敏感快照或改写原回执。
            with pytest.raises(ValueError):
                await storage.record_mcp_install_test(pending["draft_id"], "alice", context.conversation_id, pending, "verified")
            assert await provider.confirm(context, pending) == saved_failed

    asyncio.run(run())


def test_mcp_commit_rechecks_preference_and_identity_in_transaction():
    async def run():
        async with environment_api() as (_, storage):
            provider, context = await setup_provider(storage, "alice")
            initial = await provider.management.create("alice", payload())
            identifier = initial["id"]
            source = await stage(storage, context, {"url": "https://example.test/new?key=fixture-value"})
            pending = (await provider.prepare(context, source, enable=True))["installation"]
            await storage.set_mcp_preference("alice", "demo", False)
            assert (await provider.confirm(context, pending))["status"] == "error"
            assert (await storage.get_mcp_row(identifier))["version"] == 1
            assert (await storage.list_visible_mcp_rows("alice"))[0]["user_enabled"] is False
            # 输入配置不含身份；即使直接调用仓储也要按当前角色限制范围。
            provider_admin, admin = await setup_provider(storage, "admin")
            source = await stage(storage, admin, {"url": "https://example.test/admin?key=fixture-admin"})
            pending = (await provider_admin.prepare(admin, source))["installation"]
            draft = await storage.get_mcp_install_draft(pending["draft_id"], "admin", admin.conversation_id)
            from sqlalchemy import update

            from melonclaw.database.schema import users
            async with storage.engine.begin() as connection:
                await connection.execute(update(users).where(users.c.user_id == "admin").values(tenant_role="member"))
            from melonclaw.repository.mcp import McpConflictError
            with pytest.raises(McpConflictError):
                await storage.commit_mcp_install(pending["draft_id"], "admin", admin.conversation_id, pending, deepcopy(draft["payload"]["row"]))
            async with storage.engine.connect() as connection:
                result = (await connection.execute(select(mcp_install_drafts).where(mcp_install_drafts.c.id == UUID(pending["draft_id"])))) .mappings().one()
                assert result["installed_id"] is None

    asyncio.run(run())


@pytest.mark.parametrize("user_id", ["admin", "alice"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_real_chat_mcp_update_redacts_checkpoint_events_and_history(monkeypatch, tmp_path, user_id, decision):
    from langchain_core.messages import AIMessage
    from test_langfuse_chat_postgres import chat_runtime
    from test_skill_refresh import ToolModel

    from melonclaw.core import agent as agent_module

    async def run():
        async with environment_api() as (_, storage):
            async with chat_runtime(storage, tmp_path, None) as chat:
                provider = chat.runtime.mcp_install_provider
                created = await provider.management.create(user_id, {
                    **payload(headers={"set": {"Authorization": "Bearer fixture-old-auth"}}),
                    "transport": "sse", "tool_allowlist": ["search"],
                })
                conversation = await chat.create_conversation(user_id)
                cid, request = UUID(conversation["id"]), str(uuid4())
                raw = '请更新此 MCP，保留原启用和工具权限\n' + json.dumps({"mcpServers": {"demo": {
                    "url": "https://example.test/mcp?api_key=fixture-new-token",
                }}})
                clean, sources = parse_chat_mcp(raw, user_id=user_id, conversation_id=str(cid), request_id=request)
                model = ToolModel(responses=[
                    AIMessage(content="", tool_calls=[{"name": "prepare_mcp_install", "id": "prepare", "args": {"draft_id": sources[0]["id"]}}]),
                    AIMessage(content="placeholder"), AIMessage(content="已处理配置请求。"),
                ])
                original_prepare = provider.prepare

                async def prepare(*args, **kwargs):
                    result = await original_prepare(*args, **kwargs)
                    assert result["status"] == "prepared"
                    assert result["installation"]["changed_fields"] == ["url"]
                    model.responses[1] = AIMessage(content="", tool_calls=[{
                        "name": "confirm_mcp_install", "id": "confirm", "args": {"installation": result["installation"]},
                    }])
                    return result

                monkeypatch.setattr(provider, "prepare", prepare)
                monkeypatch.setattr(agent_module, "build_chat_model", lambda *args, **kwargs: model)
                provider.management._discover_tools = AsyncMock()
                prepared = await chat.prepare_message(cid, user_id, request, raw, model_id="custom:offline")
                assert prepared.content == clean
                pending_events = [event async for event in chat.stream_execution(prepared)]
                approval = next(event["request"] for event in pending_events if event["type"] == "approval_required")
                assert (await storage.get_mcp_row(created["id"]))["version"] == 1
                checkpoint = await prepared.agent.aget_state(prepared.config)
                for token in ("fixture-new-token", "fixture-old-auth"):
                    assert token not in json.dumps(pending_events, ensure_ascii=False)
                    assert token not in str(checkpoint)
                resumed, command = await chat.prepare_approval(cid, user_id, [{"type": decision}],
                    approval_batch_id=UUID(approval["approval_batch_id"]), assistant_message_id=UUID(approval["assistant_message_id"]))
                completed = [event async for event in chat.stream_execution(resumed, agent_input=command)]
                assert completed[-1]["type"] == "done"
                row = await storage.get_mcp_row(created["id"])
                assert row["version"] == (2 if decision == "approve" else 1)
                assert row["enabled"] is False and row["tool_allowlist"] == ["search"]
                assert row["transport"] == "sse"
                assert decode_credentials(row["headers"])["Authorization"] == "Bearer fixture-old-auth"
                _, messages, _ = await storage.list_messages(cid, user_id, limit=10, before_seq=None)
                for token in ("fixture-new-token", "fixture-old-auth"):
                    assert token not in json.dumps(messages, ensure_ascii=False)
                    assert token not in json.dumps(completed, ensure_ascii=False)
                    assert token not in str(await prepared.agent.aget_state(prepared.config))
                provider.management._discover_tools.assert_not_awaited()
                replay = await chat.prepare_message(cid, user_id, request, raw, model_id="custom:offline")
                assert replay.replay_message is not None
                assert (await storage.get_mcp_row(created["id"]))["version"] == row["version"]

    asyncio.run(run())
