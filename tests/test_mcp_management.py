"""MCP 权限、覆盖、凭据和 API 契约回归。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

from melonclaw.api.app import create_app
from melonclaw.api.mcp_schemas import McpCreateRequest
from melonclaw.core.mcp_credentials import decode_credentials
from melonclaw.repository.mcp import McpConflictError
from melonclaw.services.mcp import mcp_snapshot_revision, resolve_user_mcp_servers
from melonclaw.services.mcp_discovery import McpDiscoveryCoordinator
from melonclaw.services.mcp_management import McpManagementService, McpNotFoundError


class Storage:
    def __init__(self):
        self.rows = {}
        self.preferences = {}

    async def get_user_context(self, user_id):
        return SimpleNamespace(tenant_role="admin" if user_id == "admin" else "member")

    async def create_mcp_row(self, **fields):
        row = dict(fields, id=uuid4(), version=1, enabled=False)
        self.rows[str(row["id"])] = row
        return row

    async def get_mcp_row(self, identifier):
        return deepcopy(self.rows.get(identifier))

    async def list_visible_mcp_rows(self, user_id):
        return [
            {**deepcopy(row), "user_enabled": self.preferences.get((user_id, row["slug"]))}
            for row in self.rows.values()
            if row["scope"] == "global" or row["owner_user_id"] == user_id
        ]

    async def update_mcp_row(self, identifier, version, **fields):
        if self.rows[identifier]["version"] != version:
            raise McpConflictError("配置已变化。")
        self.rows[identifier].update(fields, version=version + 1)

    async def delete_mcp_row(self, identifier, version):
        if self.rows[identifier]["version"] != version:
            raise McpConflictError("配置已变化。")
        del self.rows[identifier]


def payload(**values):
    return McpCreateRequest(
        user_id="user",
        slug="demo",
        display_name="演示",
        transport="http",
        url="https://example.com/mcp",
        **values,
    ).model_dump(exclude={"user_id"})


def service():
    storage = Storage()
    return McpManagementService(SimpleNamespace(require_ready=lambda: storage, mcp_discovery=McpDiscoveryCoordinator())), storage


def test_role_visibility_shadowing_and_delete_restore():
    async def run():
        manager, storage = service()
        shared = await manager.create("admin", payload())
        private = await manager.create("alice", payload())
        assert storage.rows[shared["id"]]["owner_user_id"] is None
        assert storage.rows[private["id"]]["owner_user_id"] == "alice"
        assert (await manager.list("bob"))["items"] == []
        with pytest.raises(McpNotFoundError):
            await manager.require("bob", shared["id"])
        with pytest.raises(McpNotFoundError):
            await manager.detail("bob", private["id"])
        await manager.global_state("admin", shared["id"], 1, True)
        public = (await manager.list("bob"))["items"][0]
        assert public["effective_enabled"] and "url" not in public and "headers_keys" not in public
        rows = await storage.list_visible_mcp_rows("alice")
        assert resolve_user_mcp_servers(rows) == ({}, {})  # disabled personal still shadows
        visible = (await manager.list("alice"))["items"]
        assert next(row for row in visible if row["scope"] == "global")["shadowed"]
        storage.preferences[("alice", "demo")] = False
        await manager.delete("alice", private["id"], 1)
        assert resolve_user_mcp_servers(await storage.list_visible_mcp_rows("alice")) == ({}, {})
        await manager.global_state("admin", shared["id"], 2, False)
        assert (await manager.list("alice"))["items"] == []

    asyncio.run(run())


def test_credentials_write_only_preserve_replace_clear_and_version(monkeypatch):
    monkeypatch.setenv("MELONCLAW_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode())

    async def run():
        manager, storage = service()
        saved = await manager.create(
            "alice", payload(headers={"set": {"Authorization": "Bearer private-secret"}})
        )
        row = storage.rows[saved["id"]]
        assert "private-secret" not in str(row)
        assert "private-secret" not in str(await manager.detail("alice", saved["id"]))
        await manager.update("alice", saved["id"], 1, {"description": "changed"})
        assert decode_credentials(row["headers"])["Authorization"] == "Bearer private-secret"
        with pytest.raises(McpConflictError):
            await manager.update("alice", saved["id"], 1, {"description": "stale"})
        await manager.update(
            "alice",
            saved["id"],
            2,
            {"headers": {"set": {}, "remove": ["Authorization"], "clear": False}},
        )
        assert row["headers"] == {}

    asyncio.run(run())


def test_snapshot_delete_and_preference_invalidate_and_empty_allowlist():
    async def run():
        manager, storage = service()
        saved = await manager.create("alice", payload(tool_allowlist=[]))
        storage.rows[saved["id"]]["enabled"] = True
        rows = await storage.list_visible_mcp_rows("alice")
        _, allowlists = resolve_user_mcp_servers(rows)
        assert allowlists == {"demo": ()}
        before = mcp_snapshot_revision(rows)
        assert before != mcp_snapshot_revision([])
        rows[0]["user_enabled"] = False
        assert before != mcp_snapshot_revision(rows)

    asyncio.run(run())


def test_api_rejects_forged_scope_and_does_not_echo_invalid_secrets():
    async def run():
        manager, storage = service()
        app = create_app()
        app.state.chat = SimpleNamespace(runtime=manager.runtime)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            body = dict(
                payload(),
                user_id="alice",
                scope="global",
                headers={"set": {"Authorization": {"private-secret": "bad"}}},
            )
            response = await client.post("/api/mcp", json=body)
            assert response.status_code == 422
            assert "private-secret" not in response.text
            response = await client.post("/api/mcp", json=dict(payload(), user_id="alice"))
            assert response.status_code == 201
            identifier = response.json()["id"]
            assert storage.rows[identifier]["scope"] == "user"
            response = await client.get(f"/api/mcp/{identifier}", params={"user_id": "bob"})
            assert response.status_code == 404

    asyncio.run(run())


def test_connection_test_never_calls_tools_or_persists(monkeypatch):
    from melonclaw.services import mcp_management

    calls = []

    class Client:
        def __init__(self, config):
            calls.append(config)

        async def get_tools(self, **kwargs):
            return [SimpleNamespace(name="one", description="Read"), SimpleNamespace(name="two", description="Write")]

    monkeypatch.setattr(mcp_management, "MultiServerMCPClient", Client)

    async def run():
        manager, storage = service()
        result = await manager.test("alice", payload())
        assert result["ok"] and result["tool_count"] == 2
        assert not storage.rows
        assert len(calls) == 1

    asyncio.run(run())


def test_real_stdio_discovery_does_not_execute_business_tool(tmp_path):
    import sys

    server = tmp_path / "mcp_server.py"
    marker = tmp_path / "business_called"
    server.write_text(
        "from fastmcp import FastMCP\n"
        "from pathlib import Path\n"
        "mcp = FastMCP('verification')\n"
        "@mcp.tool\n"
        "def mutate() -> str:\n"
        f"    Path({str(marker)!r}).write_text('called')\n"
        "    return 'done'\n"
        "mcp.run()\n"
    )

    async def run():
        manager, storage = service()
        draft = payload()
        draft.update(transport="stdio", url=None, command=sys.executable, args=[str(server)])
        result = await manager.test("admin", draft)
        assert result["ok"], result["message"]
        assert result["tool_count"] == 1
        assert not marker.exists() and not storage.rows

    asyncio.run(run())


def test_discovery_reports_partial_allowlist_and_redacts_descriptions(monkeypatch):
    from melonclaw.services import mcp_management

    monkeypatch.setenv("MELONCLAW_MCP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    calls = []

    class Client:
        def __init__(self, config):
            calls.append(config)

        async def get_tools(self, **kwargs):
            return [SimpleNamespace(name="search", description="Find private-secret"),
                    SimpleNamespace(name="write", description="Write data")]

    monkeypatch.setattr(mcp_management, "MultiServerMCPClient", Client)

    async def run():
        manager, storage = service()
        saved = await manager.create("alice", payload(
            headers={"set": {"Authorization": "Bearer private-secret"}},
            tool_allowlist=["search", "removed"],
        ))
        first = await manager.discover_tools("alice", saved["id"], 1)
        assert first["ok"] and first["enabled_tool_count"] == 1
        assert first["missing_allowed_tools"] == ["removed"]
        assert first["tools"] == [
            {"name": "search", "description": "Find <redacted>", "enabled": True},
            {"name": "write", "description": "Write data", "enabled": False},
        ]
        assert "private-secret" not in str(first)
        # A cached directory never bypasses visibility/version checks.
        with pytest.raises(McpNotFoundError):
            await manager.discover_tools("bob", saved["id"], 1)
        with pytest.raises(McpConflictError):
            await manager.discover_tools("alice", saved["id"], 2)
        await manager.discover_tools("alice", saved["id"], 1)
        assert len(calls) == 1
        await manager.discover_tools("alice", saved["id"], 1, refresh=True)
        assert len(calls) == 2
        storage.rows[saved["id"]]["enabled"] = True
        assert (await manager.detail("alice", saved["id"]))["effective_enabled"]
        await manager.runtime.mcp_discovery.close()

    asyncio.run(run())
