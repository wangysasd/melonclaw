"""聊天 MCP 安装：配置隔离、身份、防篡改和真实 HITL 图。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_mcp_management import Storage
from test_skill_refresh import ToolModel

from melonclaw.core.agent import AgentContext
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS
from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
from melonclaw.repository.mappers import _now
from melonclaw.services.mcp import McpConfigError
from melonclaw.services.mcp_chat_config import parse_chat_mcp
from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator
from melonclaw.services.mcp_install import ChatMcpInstallService
from melonclaw.tool.mcp_install import build_mcp_install_tools

CONFIG = {"mcpServers": {"demo": {"url": "https://example.com/private-token?key=query-secret",
                                     "headers": {"Authorization": "Bearer test-secret"}}}}


def parse(content, request_id="req"):
    return parse_chat_mcp(content, user_id="admin", conversation_id="conversation", request_id=request_id)


def test_raw_config_is_replaced_and_retries_are_deterministic():
    content = "帮我安装\n```json\n" + json.dumps(CONFIG) + "\n```\n暂时不要测试"
    display, drafts = parse(content)
    assert "帮我安装" in display and "暂时不要测试" in display
    for secret in ("test-secret", "private-token", "query-secret", "Authorization"):
        assert secret not in display
    assert parse(content) == (display, drafts)
    changed = deepcopy(CONFIG)
    changed["mcpServers"]["demo"]["headers"]["Authorization"] = "Bearer changed"
    assert parse(json.dumps(changed))[1][0]["id"] != drafts[0]["id"]
    assert drafts[0]["payload"]["transport"] == "http"
    assert parse("请解释 MCP 是什么") == ("请解释 MCP 是什么", [])


@pytest.mark.parametrize("content", [
    '{"headers":{"Authorization":"Bearer hidden"}}',
    '{"mcpServers":{"demo":{"url":"https://x.test","url":"https://y.test"}}}',
    '{"mcpServers":{"demo":{"command":"npx","args":["package"]}}}',
    '{"mcpServers":{"demo":{"url":"https://x.test","headers":{"Authorization":"${SERVER_KEY}"}}}}',
    '{"mcpServers":{"demo":{"url":"https://x.test","owner_user_id":"other"}}}',
    '{"mcpServers":{"demo":{"url":"https://x.test","env":{"KEY":"secret"}}}}',
    '{"mcpServers":{"demo":{"url":"https://x.test","type":"sse","transport":"http"}}}',
    '```json\n{"mcpServers": invalid}\n```',
])
def test_invalid_config_fails_closed_without_echo(content):
    with pytest.raises(McpConfigError) as caught:
        parse(content)
    assert "SERVER_KEY" not in str(caught.value)
    assert content not in str(caught.value)


class DraftStorage(Storage):
    def __init__(self):
        super().__init__()
        self.drafts = {}
        self.commits = 0

    async def get_mcp_install_draft(self, identifier, user_id, conversation_id):
        draft = self.drafts.get(identifier)
        if draft and draft["user_id"] == user_id and draft["conversation_id"] == conversation_id and draft["expires_at"] > _now():
            return deepcopy(draft)
        return None

    async def prepare_mcp_install_draft(self, source, installation, payload):
        self.drafts[installation["draft_id"]] = dict(source, payload=payload, installation=installation)

    async def commit_mcp_install(self, identifier, user_id, conversation_id, installation, row):
        self.commits += 1
        result = await self.create_mcp_row(**row)
        result["enabled"] = installation["enable"]
        self.drafts[identifier].update(installed_id=str(result["id"]), payload={})
        self.preferences[(user_id, row["slug"])] = installation["enable"]
        return str(result["id"])


@pytest.fixture
def setup():
    storage = DraftStorage()
    cid = str(uuid4())
    context = AgentContext(user_id="admin", tenant_id="tenant", tenant_name="租户", conversation_id=cid)
    storage.get_conversation = AsyncMock(return_value={"id": cid, "project_id": None})
    user = SimpleNamespace(user_id="admin", tenant_id="tenant", tenant_role="admin")
    chat = SimpleNamespace(
        runtime=SimpleNamespace(require_ready=lambda: storage, mcp_discovery=McpDiscoveryCoordinator()),
        conversations=SimpleNamespace(resolve_user=AsyncMock(return_value=user)),
    )
    _, items = parse(json.dumps(CONFIG))
    source = items[0]
    storage.drafts[source["id"]] = dict(user_id="admin", conversation_id=cid, payload=source["payload"],
                                        installation=None, installed_id=None, expires_at=_now() + timedelta(hours=24))
    return ChatMcpInstallService(chat), storage, context, source["id"]


def test_admin_install_is_personal_and_idempotent(setup):
    provider, storage, context, source = setup

    async def run():
        preview = await provider.prepare(context, source, True, ["search"])
        assert preview["status"] == "prepared"
        installation = preview["installation"]
        assert installation["scope"] == "user"
        assert "private-token" not in json.dumps(preview) and "query-secret" not in json.dumps(preview)
        assert not storage.rows
        result = await provider.confirm(context, installation)
        assert result["status"] == "installed"
        row = storage.rows[result["id"]]
        assert row["owner_user_id"] == "admin" and row["scope"] == "user"
        assert row["tool_allowlist"] == ["search"] and row["enabled"]
        assert await provider.confirm(context, installation) == result
        assert storage.commits == 1
        assert (await provider.prepare(context, source, True, None))["status"] == "error"
    asyncio.run(run())


def test_draft_ownership_expiry_and_manifest_are_enforced(setup):
    provider, storage, context, source = setup

    async def run():
        preview = await provider.prepare(context, source, False, [])
        manifest = preview["installation"]
        for key, value in [("enable", True), ("connection", "https://other.test"), ("tool_allowlist", None)]:
            assert (await provider.confirm(context, {**manifest, key: value}))["status"] == "error"
        assert (await provider.confirm(replace(context, conversation_id=str(uuid4())), manifest))["status"] == "error"
        provider.chat.conversations.resolve_user.return_value = SimpleNamespace(user_id="bob", tenant_id="tenant")
        assert (await provider.confirm(replace(context, user_id="bob"), manifest))["status"] == "error"
        provider.chat.conversations.resolve_user.return_value = SimpleNamespace(user_id="admin", tenant_id="other")
        assert (await provider.confirm(context, manifest))["status"] == "error"
        provider.chat.conversations.resolve_user.return_value = SimpleNamespace(user_id="admin", tenant_id="tenant")
        storage.drafts[manifest["draft_id"]]["expires_at"] = _now() - timedelta(seconds=1)
        assert (await provider.confirm(context, manifest))["status"] == "error"
        assert not storage.rows
    asyncio.run(run())


def test_tools_hide_identity_and_cannot_enter_ptc(setup):
    provider, _, _, _ = setup
    for tool in build_mcp_install_tools(provider):
        fields = tool.tool_call_schema.model_json_schema()["properties"]
        assert not {"user_id", "tenant_id", "conversation_id", "runtime", "payload", "url", "headers"} & fields.keys()
        assert tool.name not in INTERPRETER_PTC_TOOLS
    for name in ("test_mcp_install", "confirm_mcp_install"):
        assert SENSITIVE_TOOL_INTERRUPTS[name]["allowed_decisions"] == ["approve", "reject"]


@pytest.mark.parametrize("tool_name", ["test_mcp_install", "confirm_mcp_install"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_real_graph_waits_for_approval(setup, tmp_path, tool_name, decision):
    provider, storage, context, source = setup
    provider.management._discover_tools = AsyncMock(return_value={"ok": True, "tool_names": ["search"]})

    async def run():
        preview = await provider.prepare(context, source, True, None)
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": tool_name, "id": "call",
                                               "args": {"installation": preview["installation"]}}]),
            AIMessage(content="done"),
        ])
        graph = create_deep_agent(model=model, tools=build_mcp_install_tools(provider),
                                  backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
                                  context_schema=AgentContext, interrupt_on=SENSITIVE_TOOL_INTERRUPTS,
                                  checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": context.conversation_id}}
        result = await graph.ainvoke({"messages": [{"role": "user", "content": "安装 MCP"}]}, config, context=context)
        assert result["__interrupt__"] and not storage.rows
        provider.management._discover_tools.assert_not_awaited()
        await graph.ainvoke(Command(resume={"decisions": [{"type": decision}]}), config, context=context)
        assert bool(storage.rows) == (tool_name == "confirm_mcp_install" and decision == "approve")
        assert provider.management._discover_tools.await_count == int(tool_name == "test_mcp_install" and decision == "approve")
    asyncio.run(run())


def test_execution_stores_only_draft_reference_and_retry_replays():
    from test_execution_prepare import _service, _Storage

    from melonclaw.repository import RequestRecord

    async def run():
        storage = _Storage()
        service, _ = _service(storage)
        storage.get_incomplete_assistant = AsyncMock(return_value=None)
        storage.stage_mcp_drafts = AsyncMock()
        storage.create_message_pair = AsyncMock(return_value=SimpleNamespace(attachments=[]))
        service.runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(
            profile_id="custom:model", provider="provider", model_name="model", display_name="Model", input_modalities=("text",),
        ))
        service._execution_from_pair = lambda *args, **kwargs: "prepared"
        cid = uuid4()
        content = "安装这个 MCP\n```json\n" + json.dumps(CONFIG) + "\n```"
        from unittest.mock import patch
        with patch("melonclaw.services.execution.aget_pending_interaction", AsyncMock(return_value=None)):
            assert await service.prepare_message(cid, "admin", "req", content) == "prepared"
        saved_content = storage.create_message_pair.await_args.args[4]
        assert "test-secret" not in saved_content and "private-token" not in saved_content
        assert "MCP 配置草稿" in saved_content
        assert storage.stage_mcp_drafts.await_count == 1
        storage.existing = RequestRecord(
            request_id="req", content=saved_content, user_message={"id": str(uuid4()), "display_metadata": {}},
            assistant_message={"id": str(uuid4()), "request_id": "req", "status": "completed", "content": "done", "assistant_steps": [], "display_metadata": {}},
        )
        service.runtime.model_for_message = AsyncMock(return_value=SimpleNamespace(public_dict=lambda: {"id": "model"}))
        result = await service.prepare_message(cid, "admin", "req", content)
        assert result.replay_message is not None
        assert storage.stage_mcp_drafts.await_count == 1
    asyncio.run(run())


def test_connection_reflections_redact_url_and_header_credentials():
    from melonclaw.core.mcp_config import redact_mcp_connection_text

    config = CONFIG["mcpServers"]["demo"]
    reflected = "test-secret private-token query-secret " + config["url"]
    result = redact_mcp_connection_text(reflected, config)
    assert all(secret not in result for secret in ("test-secret", "private-token", "query-secret", config["url"]))


def test_invalid_message_schema_does_not_echo_config():
    import httpx

    from melonclaw.api.app import create_app

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
            response = await client.post(f"/api/conversations/{uuid4()}/messages", json={
                "user_id": "admin", "request_id": str(uuid4()), "content": json.dumps(CONFIG) + "x" * 12000,
            })
        assert response.status_code == 422
        assert "test-secret" not in response.text and "private-token" not in response.text
    asyncio.run(run())


def test_real_prepare_tool_defaults_do_not_require_optional_arguments(setup, tmp_path):
    provider, storage, context, source = setup

    async def run():
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "prepare_mcp_install", "id": "prepare",
                                               "args": {"draft_id": source}}]),
            AIMessage(content="preview"),
        ])
        graph = create_deep_agent(model=model, tools=build_mcp_install_tools(provider),
                                  backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
                                  context_schema=AgentContext, interrupt_on=SENSITIVE_TOOL_INTERRUPTS,
                                  checkpointer=InMemorySaver())
        result = await graph.ainvoke({"messages": [{"role": "user", "content": "安装 MCP"}]},
                                     {"configurable": {"thread_id": context.conversation_id}}, context=context)
        response = json.loads(next(message.content for message in result["messages"] if message.type == "tool"))
        assert response["status"] == "prepared"
        assert response["installation"]["enable"] is True
        assert response["installation"]["tool_allowlist"] is None
        assert not storage.rows
    asyncio.run(run())


def test_plain_pasted_json_preserves_surrounding_intent_and_quoted_braces():
    config = deepcopy(CONFIG)
    config["mcpServers"]["demo"]["headers"]["X-Key"] = "brace}inside{quoted"
    display, drafts = parse("帮我安装 " + json.dumps(config) + " 暂不启用")
    assert len(drafts) == 1 and display.startswith("帮我安装") and display.endswith("暂不启用")
    assert "brace}inside" not in display and "test-secret" not in display
    assert parse("普通说明 {\"count\":2}") == ('普通说明 {"count":2}', [])
