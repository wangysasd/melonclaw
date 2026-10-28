"""聊天 MCP 仅更新明确字段，保留权限，凭据和审批快照不向模型暴露。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_chat_mcp_install
from test_mcp_management import payload

from melonclaw.core.mcp_credentials import decode_credentials
from melonclaw.services.mcp_chat_config import parse_chat_mcp

setup = test_chat_mcp_install.setup


def stage(provider, storage, context, config):
    _, items = parse_chat_mcp(json.dumps({"mcpServers": {"demo": config}}), user_id=context.user_id,
                              conversation_id=context.conversation_id, request_id="update")
    identifier = items[0]["id"]
    template = next(iter(storage.drafts.values()))
    storage.drafts[identifier] = {**deepcopy(template), "payload": items[0]["payload"], "installation": None, "installed_id": None, "user_id": context.user_id, "conversation_id": context.conversation_id}
    return identifier


async def existing(provider, context, *, enabled=False):
    record = await provider.management.create(context.user_id, {
        **payload(headers={"set": {"Authorization": "Bearer fixture-old", "X-Custom": "fixture-custom"}}),
        "transport": "sse", "description": "原有说明", "tool_allowlist": ["search"],
    })
    await provider.management.storage.update_mcp_row(record["id"], 1, enabled=enabled)
    return record["id"]


def test_url_credential_rotation_preserves_identity_type_metadata_auth_and_permissions(setup):
    provider, storage, context, _ = setup

    async def run():
        identifier = await existing(provider, context)
        source = stage(provider, storage, context, {"url": "https://example.test/mcp?api_key=fixture-new-token"})
        original = deepcopy(storage.rows[identifier])
        preview = await provider.prepare(context, source)
        manifest = preview["installation"]
        assert manifest["operation"] == "update" and manifest["target_id"] == identifier
        assert manifest["expected_version"] == 2 and manifest["changed_fields"] == ["url"]
        assert manifest["transport"] == "sse" and manifest["enable"] is False
        assert manifest["tool_allowlist"] == ["search"]
        assert "fixture-new-token" not in json.dumps(preview)
        assert "fixture-old" not in json.dumps(preview)
        provider.management._discover_tools = AsyncMock()
        result = await provider.confirm(context, manifest)
        assert result["status"] == "updated" and result["connection_status"] == "untested"
        assert result["id"] == identifier and result["version"] == 3
        assert len(storage.rows) == 1
        row = storage.rows[identifier]
        assert row["url"].endswith("fixture-new-token")
        for key in ("headers", "transport", "enabled", "tool_allowlist", "description", "display_name", "created_by"):
            assert row[key] == original[key]
        provider.management._discover_tools.assert_not_awaited()
        assert await provider.confirm(context, manifest) == result
        assert storage.commits == 1
        assert "fixture-new-token" not in str(storage.drafts[manifest["draft_id"]]["payload"])
        repeat = await provider.prepare(context, source)
        assert repeat["status"] == "unchanged"
        assert repeat["installation"]["changed_fields"] == []

    asyncio.run(run())


def test_header_patches_preserve_omitted_keys_and_empty_map_explicitly_clears(setup):
    provider, storage, context, _ = setup

    async def run():
        identifier = await existing(provider, context)
        source = stage(provider, storage, context, {"headers": {"authorization": "Bearer fixture-replaced"}})
        prepared = await provider.prepare(context, source)
        assert prepared["installation"]["changed_fields"] == ["headers"]
        result = await provider.confirm(context, prepared["installation"])
        assert result["status"] == "updated"
        assert decode_credentials(storage.rows[identifier]["headers"]) == {
            "authorization": "Bearer fixture-replaced", "X-Custom": "fixture-custom",
        }
        source = stage(provider, storage, context, {"headers": {}})
        prepared = await provider.prepare(context, source)
        assert (await provider.confirm(context, prepared["installation"]))["status"] == "updated"
        assert storage.rows[identifier]["headers"] == {}
        assert storage.rows[identifier]["url"] == "https://example.com/mcp"

    asyncio.run(run())


def test_unchanged_config_and_whitelist_change_require_explicit_option(setup):
    provider, storage, context, _ = setup

    async def run():
        identifier = await existing(provider, context)
        source = stage(provider, storage, context, {"url": storage.rows[identifier]["url"]})
        prepared = await provider.prepare(context, source)
        assert prepared["status"] == "unchanged"
        assert storage.rows[identifier]["version"] == 2
        restricted = await provider.prepare(context, source, tool_allowlist=[])
        assert restricted["installation"]["tool_allowlist"] == []
        assert restricted["installation"]["changed_fields"] == ["tool_allowlist"]
        explicit = await provider.prepare(context, source, allow_all_tools=True)
        assert explicit["installation"]["tool_allowlist"] is None
        assert (await provider.prepare(context, source, tool_allowlist=[], allow_all_tools=True))["status"] == "error"
        provider.management._discover_tools = AsyncMock(return_value={"ok": False, "tool_names": [], "message": "鉴权失败"})
        tested = await provider.test(context, restricted["installation"])
        assert tested["connection_status"] == "failed"
        result = await provider.confirm(context, restricted["installation"])
        assert result["status"] == "updated" and result["connection_status"] == "failed"
        assert storage.rows[identifier]["tool_allowlist"] == []

    asyncio.run(run())


def test_update_version_conflict_rejects_test_and_save_without_touching_old_config(setup):
    provider, storage, context, _ = setup

    async def run():
        identifier = await existing(provider, context)
        source = stage(provider, storage, context, {"url": "https://example.test/new-token"})
        prepared = await provider.prepare(context, source)
        await storage.update_mcp_row(identifier, 2, description="另一入口修改")
        provider.management._discover_tools = AsyncMock()
        assert (await provider.test(context, prepared["installation"]))["status"] == "error"
        assert (await provider.confirm(context, prepared["installation"]))["status"] == "error"
        assert storage.rows[identifier]["url"] == "https://example.com/mcp"
        provider.management._discover_tools.assert_not_awaited()
        for key, value in (("expected_version", 3), ("target_id", "forged"), ("changed_fields", []), ("operation", "install")):
            forged = {**prepared["installation"], key: value}
            assert (await provider.confirm(context, forged))["status"] == "error"
        assert storage.commits == 0

    asyncio.run(run())


def test_only_query_token_change_is_an_update_and_provided_fields_survive_normalization():
    one = {"mcpServers": {"demo": {"url": "https://example.test/mcp?api_key=fixture-one"}}}
    two = deepcopy(one)
    two["mcpServers"]["demo"]["url"] = "https://example.test/mcp?api_key=fixture-two"
    kwargs = dict(user_id="user", conversation_id="conversation", request_id="req")
    clean, draft = parse_chat_mcp(json.dumps(one), **kwargs)
    assert draft[0]["payload"]["provided_fields"] == ["url"]
    assert "fixture-one" not in clean
    assert draft[0]["id"] != parse_chat_mcp(json.dumps(two), **kwargs)[1][0]["id"]


def test_member_creates_own_shadow_and_updates_only_own_record_preserving_preferences(setup):
    provider, storage, context, _ = setup

    async def run():
        shared_id = await existing(provider, context, enabled=True)
        context_member = replace(context, user_id="alice")
        provider.chat.conversations.resolve_user.return_value = SimpleNamespace(user_id="alice", tenant_id=context.tenant_id)
        source = stage(provider, storage, context_member, {"url": "https://example.test/alice?key=fixture-first"})
        prepared = await provider.prepare(context_member, source)
        manifest = prepared["installation"]
        assert manifest["scope"] == "user" and manifest["operation"] == "install"
        assert manifest["shadows_global"] is True and manifest["target_id"] is None
        first = await provider.confirm(context_member, manifest)
        assert first["status"] == "installed"
        personal_id = first["id"]
        storage.preferences[("alice", "demo")] = False
        source = stage(provider, storage, context_member, {"url": "https://example.test/alice?key=fixture-rotated"})
        prepared = await provider.prepare(context_member, source)
        assert prepared["installation"]["target_id"] == personal_id
        assert prepared["installation"]["changed_fields"] == ["url"]
        assert (await provider.confirm(context_member, prepared["installation"]))["status"] == "updated"
        assert storage.preferences[("alice", "demo")] is False
        assert storage.rows[shared_id]["url"] == "https://example.com/mcp"
        assert len(storage.rows) == 2
        explicit = await provider.prepare(context_member, source, enable=True)
        assert "personally_enabled" in explicit["installation"]["changed_fields"]
        assert (await provider.confirm(context_member, explicit["installation"]))["status"] == "updated"
        assert storage.preferences[("alice", "demo")] is True

    asyncio.run(run())
