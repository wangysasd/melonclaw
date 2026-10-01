"""MCP 资源用例：按角色归属、版本化编辑、只写凭据与连接测试。"""

from __future__ import annotations

import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient

from melonclaw.core.mcp_config import redact_mcp_connection_text, select_mcp_tool_names
from melonclaw.core.mcp_credentials import decode_credentials, encode_credentials
from melonclaw.repository.mcp import McpConflictError
from melonclaw.services.mcp import (
    MCP_SLUG_RE,
    McpConfigError,
    resolved_mcp_config,
    selected_mcp_rows,
    validate_mcp_payload,
)

ADMIN_ROLES = {"admin", "owner"}


class McpNotFoundError(ValueError):
    status_code = 404


class McpPermissionError(ValueError):
    status_code = 403


def public_mcp(row, *, manageable, own_names, global_names, active_ids):
    shadowed = row["scope"] == "global" and row["slug"] in own_names
    personal = row["user_enabled"] is not False
    reason = (
        "已被我的配置替代"
        if shadowed
        else "全员已停用"
        if not row["enabled"] and row["scope"] == "global"
        else "待添加"
        if not row["enabled"]
        else "我不使用"
        if not personal
        else None
    )
    result = {
        key: row[key]
        for key in (
            "slug",
            "display_name",
            "description",
            "scope",
            "transport",
            "enabled",
            "version",
        )
    }
    result.update(
        id=str(row["id"]),
        personally_enabled=personal,
        effective_enabled=str(row["id"]) in active_ids,
        shadowed=shadowed,
        shadows_global=row["scope"] == "user" and row["slug"] in global_names,
        unavailable_reason=reason,
        can_edit=manageable,
        can_delete=manageable,
        can_test=manageable,
    )
    if manageable:
        result.update({key: row[key] for key in ("url", "command", "args", "tool_allowlist")})
        result.update(headers_keys=sorted(row["headers"]), env_keys=sorted(row["env"]))
    return result


def merge_credentials(existing: dict, patch: dict, scope: str) -> dict:
    if patch["clear"] and (patch["set"] or patch["remove"]):
        raise McpConfigError("清空凭据不能同时替换或移除键。")
    result = {} if patch["clear"] else dict(existing)
    for key in patch["remove"]:
        result.pop(key, None)
    result.update(encode_credentials(patch["set"], scope))
    return result


class McpManagementService:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def storage(self):
        return self.runtime.require_ready()

    async def role(self, user_id):
        context = await self.storage.get_user_context(user_id)
        if context is None:
            raise McpPermissionError("用户不存在或已停用。")
        return context.tenant_role

    async def list(self, user_id):
        admin = await self.role(user_id) in ADMIN_ROLES
        rows = await self.storage.list_visible_mcp_rows(user_id)
        visible = [row for row in rows if row["scope"] == "user" or row["enabled"] or admin]
        own = {row["slug"] for row in rows if row["scope"] == "user"}
        global_names = {row["slug"] for row in visible if row["scope"] == "global"}
        active = {str(row["id"]) for row in selected_mcp_rows(rows)}
        return {
            "items": [
                public_mcp(
                    row,
                    manageable=admin if row["scope"] == "global" else True,
                    own_names=own,
                    global_names=global_names,
                    active_ids=active,
                )
                for row in visible
            ]
        }

    async def require(self, user_id, identifier, *, manage=False, version=None):
        admin = await self.role(user_id) in ADMIN_ROLES
        row = await self.storage.get_mcp_row(identifier)
        if (
            row is None
            or (row["scope"] == "user" and row["owner_user_id"] != user_id)
            or (row["scope"] == "global" and not row["enabled"] and not admin)
        ):
            raise McpNotFoundError("MCP 服务不存在。")
        if manage and row["scope"] == "global" and not admin:
            raise McpPermissionError("只有管理员可以管理全局 MCP。")
        if version is not None and version != row["version"]:
            raise McpConflictError("配置已变化，请刷新后重试。")
        return row

    async def detail(self, user_id, identifier):
        await self.require(user_id, identifier, manage=True)
        return next(
            item for item in (await self.list(user_id))["items"] if item["id"] == identifier
        )

    def validate(self, row):
        if not MCP_SLUG_RE.fullmatch(row["slug"]) or not row["display_name"].strip():
            raise McpConfigError("请填写名称和合法的小写服务标识。")
        validate_mcp_payload(
            scope=row["scope"],
            transport=row["transport"],
            url=row["url"],
            command=row["command"],
            args=row["args"],
            headers=decode_credentials(row["headers"]),
            env=decode_credentials(row["env"]),
        )
        names = row["tool_allowlist"]
        if names is not None and (
            any(not name.strip() for name in names) or len(names) != len(set(names))
        ):
            raise McpConfigError("工具白名单不能包含空名称或重复名称。")

    async def prepare(self, user_id, payload, *, identifier=None, version=None):
        if identifier:
            row = await self.require(user_id, identifier, manage=True, version=version)
            row = dict(row)
        else:
            scope = "global" if await self.role(user_id) in ADMIN_ROLES else "user"
            row = {
                "scope": scope,
                "owner_user_id": None if scope == "global" else user_id,
                "created_by": user_id,
                "headers": {},
                "env": {},
            }
        for key in (
            "slug",
            "display_name",
            "description",
            "transport",
            "url",
            "command",
            "args",
            "tool_allowlist",
        ):
            if key in payload:
                if identifier and key == "slug" and payload[key] != row["slug"]:
                    raise McpConfigError("服务标识不可修改。")
                row[key] = payload[key]
        for key in ("headers", "env"):
            if key in payload:
                row[key] = merge_credentials(row[key], payload[key], row["scope"])
        self.validate(row)
        return row

    async def create(self, user_id, payload):
        row = await self.prepare(user_id, payload)
        saved = await self.storage.create_mcp_row(**row)
        return {"id": str(saved["id"]), "version": saved["version"]}

    async def update(self, user_id, identifier, version, payload):
        row = await self.prepare(user_id, payload, identifier=identifier, version=version)
        fields = {
            key: row[key]
            for key in (
                "display_name",
                "description",
                "transport",
                "url",
                "command",
                "args",
                "headers",
                "env",
                "tool_allowlist",
            )
        }
        await self.storage.update_mcp_row(identifier, version, **fields)
        return {"id": identifier, "version": version + 1}

    async def global_state(self, user_id, identifier, version, enabled):
        row = await self.require(user_id, identifier, manage=True, version=version)
        if row["scope"] != "global":
            raise McpConfigError("全员状态仅适用于全局配置。")
        await self.storage.update_mcp_row(identifier, version, enabled=enabled)
        return {"ok": True}

    async def add(self, user_id, identifier, version):
        await self.require(user_id, identifier, version=version)
        await self.storage.add_mcp_row(user_id, identifier, version)
        return {"ok": True}

    async def preference(self, user_id, slug, enabled):
        await self.role(user_id)
        await self.storage.set_mcp_preference(user_id, slug, enabled)
        return {"ok": True}

    async def delete(self, user_id, identifier, version):
        row = await self.require(user_id, identifier, manage=True, version=version)
        await self.storage.delete_mcp_row(identifier, version)
        visible = (await self.list(user_id))["items"]
        return {"ok": True, "remaining": [item for item in visible if item["slug"] == row["slug"]]}

    async def discover_tools(
        self, user_id, identifier, version, *, background=False, refresh=False
    ):
        row = await self.require(user_id, identifier, version=version)
        result = await self._discover_tools(row, background=background, refresh=refresh)
        enabled, missing = select_mcp_tool_names(result["tool_names"], row["tool_allowlist"])
        enabled_names = set(enabled)
        return {
            **result,
            "enabled_tool_count": len(enabled) if result["ok"] else 0,
            "missing_allowed_tools": missing if result["ok"] else [],
            "tools": [
                {**tool, "enabled": tool["name"] in enabled_names}
                for tool in result["tool_details"]
            ],
        }

    async def test(self, user_id, payload=None, *, identifier=None, version=None):
        row = (
            await self.require(user_id, identifier, manage=True, version=version)
            if payload is None
            else await self.prepare(user_id, payload, identifier=identifier, version=version)
        )
        return await self._discover_tools(row, refresh=True, cache=False)

    async def _discover_tools(self, row, *, background=False, refresh=False, cache=True):
        key = (str(row["id"]), row["version"]) if cache and row["transport"] != "stdio" else None
        return await self.runtime.mcp_discovery.discover(
            lambda: self._probe(row), key=key, background=background, refresh=refresh
        )

    async def _probe(self, row):
        try:
            config = resolved_mcp_config(row)
            async with asyncio.timeout(15):
                client = MultiServerMCPClient({row["slug"]: config})
                discovered = await client.get_tools(server_name=row["slug"])
            tool_names = sorted(
                {
                    name
                    for tool in discovered
                    if isinstance((name := getattr(tool, "name", None)), str) and name.strip()
                }
            )

            def description(tool):
                return redact_mcp_connection_text(getattr(tool, "description", "") or "", config)[
                    :2000
                ]

            descriptions = {
                tool.name: description(tool) for tool in discovered if hasattr(tool, "name")
            }
            return {
                "ok": True,
                "error_code": None,
                "tool_count": len(tool_names),
                "tool_names": tool_names,
                "tool_details": [
                    {"name": name, "description": descriptions[name]} for name in tool_names
                ],
                "message": f"连接成功，发现 {len(tool_names)} 个工具。",
            }
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return {
                "ok": False,
                "error_code": test_error_code(exc),
                "tool_count": 0,
                "tool_names": [],
                "tool_details": [],
                "message": test_error(exc),
            }


def test_error(exc):
    """只分类，不回显第三方错误或凭据。"""
    if isinstance(exc, BaseExceptionGroup):
        return "；".join(dict.fromkeys(test_error(item) for item in exc.exceptions))
    if isinstance(exc, TimeoutError):
        return "连接超时，请检查地址与服务状态。"
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) in (401, 403):
        return "鉴权失败，请检查凭据。"
    return "连接或协议校验失败，请检查配置、凭据和服务状态。"


def test_error_code(exc):
    if isinstance(exc, BaseExceptionGroup):
        codes = {test_error_code(item) for item in exc.exceptions}
        return (
            "timeout"
            if "timeout" in codes
            else "authentication"
            if "authentication" in codes
            else "connection"
        )
    if isinstance(exc, TimeoutError):
        return "timeout"
    return (
        "authentication"
        if getattr(getattr(exc, "response", None), "status_code", None) in (401, 403)
        else "connection"
    )
