"""自定义工具与可选 MCP 工具的装配。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Collection, Mapping
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from melonclaw.core.config import Settings
from melonclaw.core.mcp_config import (
    redact_mcp_connection_text,
    redact_mcp_sensitive_text,
    select_mcp_tool_names,
)
from melonclaw.tool.search import internet_search

MCP_CATALOG_TOOL_NAME = "list_mcp_tools"


def _format_mcp_exception(exc: BaseException) -> str:
    """展开 MCP 连接异常，保留可诊断的状态。

    不回显完整请求信息，避免错误文本携带认证 URL。
    """

    if isinstance(exc, BaseExceptionGroup):
        details = [
            _format_mcp_exception(child)
            for child in exc.exceptions
            if not isinstance(child, asyncio.CancelledError)
        ]
        unique_details = list(dict.fromkeys(detail for detail in details if detail))
        return "；".join(unique_details) or type(exc).__name__

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return f"HTTP {status_code}"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "连接超时"
    if type(exc).__name__ in {"ConnectError", "ConnectionError"}:
        return "连接失败"
    return "MCP 连接或协议错误"


def _safe_mcp_exception(exc: BaseException) -> str:
    """返回可写入日志和清单的脱敏 MCP 错误摘要。"""

    return redact_mcp_sensitive_text(_format_mcp_exception(exc), os.environ)


def build_custom_tools(settings: Settings) -> list[BaseTool]:
    """在装配入口生成完整工具定义，供选择器与 Agent 共用。"""

    return [StructuredTool.from_function(internet_search)]


def build_mcp_catalog_tool(
    servers: Mapping[str, Mapping[str, Any]],
    tools_by_server: Mapping[str, Collection[str]],
    failures: Mapping[str, str] | None = None,
) -> StructuredTool:
    """构造只读的运行时 MCP 服务与发现状态清单工具。"""

    failed = failures or {}
    server_catalog = []
    for server_name, server_config in sorted(servers.items()):
        item: dict[str, Any] = {
            "name": server_name,
            "transport": str(server_config.get("transport", "")),
            "tools": list(tools_by_server.get(server_name, ())),
            "status": "failed" if server_name in failed else "ready",
        }
        if server_name in failed:
            item["error"] = failed[server_name]
        server_catalog.append(item)

    def list_mcp_tools() -> dict[str, Any]:
        """列出当前运行时配置的 MCP 服务、发现状态和可用工具。"""

        return {
            "servers": server_catalog,
            "server_count": len(server_catalog),
            "tool_count": sum(len(item["tools"]) for item in server_catalog),
            "failed_server_count": sum(
                item["status"] == "failed" for item in server_catalog
            ),
        }

    return StructuredTool.from_function(
        func=list_mcp_tools,
        metadata={"mcp_failures": bool(failed)},
        name=MCP_CATALOG_TOOL_NAME,
        description=(
            "只读查询当前运行时配置的 MCP 服务、传输方式、发现状态和"
            "工具名称。"
            "用户询问 MCP 配置、可用 MCP 或工具清单时必须调用。"
        ),
    )


async def _load_mcp_tools_with_catalog(
    servers: dict[str, dict[str, Any]],
    tool_allowlists: dict[str, tuple[str, ...]] | None = None,
) -> tuple[list[BaseTool], dict[str, tuple[str, ...]], dict[str, str]]:
    """发现 MCP 工具，同时记录实际暴露给 Agent 的工具清单。"""

    valid = {name: config for name, config in servers.items() if not config.get("configuration_error")}
    client = MultiServerMCPClient(valid)

    async def discover(name):
        async with asyncio.timeout(15):
            return await client.get_tools(server_name=name)

    discovered_by_server = await asyncio.gather(*(discover(name) for name in valid), return_exceptions=True)
    tools, catalog = [], {}
    failures = {name: "凭据或配置无法解析，请检查配置。" for name in servers if name not in valid}
    for server_name, discovered in zip(valid, discovered_by_server, strict=True):
        if isinstance(discovered, BaseException):
            if not isinstance(discovered, Exception):
                raise discovered
            failures[server_name] = _safe_mcp_exception(discovered)
            continue
        allowlist = (tool_allowlists or {}).get(server_name)
        by_name = {tool.name: tool for tool in discovered}
        selected_names, _ = select_mcp_tool_names(list(by_name), allowlist)
        selected = [by_name[name] for name in selected_names]
        try:
            wrapped = [wrap_mcp_tool(server_name, tool, valid[server_name]) for tool in selected]
        except Exception:
            failures[server_name] = "工具定义无效，请检查 MCP 服务。"
            continue
        tools.extend(wrapped)
        catalog[server_name] = tuple(tool.name for tool in wrapped)
    return tools, catalog, failures



def wrap_mcp_tool(server_name, original, config):
    """隔离工具命名并封装执行错误；所有外部 MCP 工具由 HITL 审批。"""
    # Names bind pending checkpoint calls to the exact connection configuration. A
    # resumed graph with changed credentials/address cannot execute the old name.
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    raw_name = f"mcp__{server_name}__{original.name}__{fingerprint}"
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_name)
    if len(name) > 64 or name != raw_name:
        name = name[:47] + "_" + hashlib.sha256(raw_name.encode()).hexdigest()[:16]

    def sanitize(value):
        if isinstance(value, str):
            return redact_mcp_connection_text(value, config)
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        return value

    async def call(**kwargs):
        try:
            return sanitize(await original.ainvoke(kwargs))
        except asyncio.CancelledError:
            raise
        except Exception:
            return "MCP 工具执行失败，请检查服务状态或调整参数。"

    return StructuredTool.from_function(coroutine=call, name=name, description=sanitize(original.description),
                                       args_schema=original.args_schema, metadata={"mcp": True})

async def load_mcp_tools(
    servers: dict[str, dict[str, Any]],
    tool_allowlists: dict[str, tuple[str, ...]] | None = None,
) -> list[BaseTool]:
    """加载多个 MCP 服务的工具，并跳过发现失败的服务。"""

    if not servers:
        return []

    try:
        tools, _, _ = await _load_mcp_tools_with_catalog(servers, tool_allowlists)
        return tools
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        server_names = ", ".join(sorted(servers))
        safe_detail = _safe_mcp_exception(exc)
        raise RuntimeError(
            f"加载 MCP 工具失败（服务：{server_names}）：{safe_detail}"
        ) from None


async def build_agent_tools(
    settings: Settings,
    *,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    mcp_tool_allowlists: dict[str, tuple[str, ...]] | None = None,
) -> list[BaseTool]:
    """异步组合已具备名称、描述与参数 schema 的应用和 MCP 工具。

    MCP 配置由运行时按当前用户从数据库解析后显式传入。
    """

    servers = mcp_servers or {}
    allowlists = mcp_tool_allowlists or {}

    tools: list[BaseTool] = build_custom_tools(settings)
    if not servers:
        tools.append(build_mcp_catalog_tool({}, {}))
        return tools

    mcp_tools, catalog, failures = await _load_mcp_tools_with_catalog(
        servers,
        allowlists,
    )

    tools.extend(mcp_tools)
    if failures:
        failed_text = "、".join(
            f"{name}（{detail}）" for name, detail in sorted(failures.items())
        )
        print(f"MCP 服务部分加载失败，已跳过：{failed_text}")
    tools.append(build_mcp_catalog_tool(servers, catalog, failures))
    return tools
