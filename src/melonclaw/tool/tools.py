"""自定义工具与可选 MCP 工具的装配。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Collection, Mapping
from typing import Any

from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from melonclaw.core.config import Settings
from melonclaw.core.mcp_config import redact_mcp_sensitive_text
from melonclaw.tool.search import internet_search

ToolDefinition = Callable[..., Any] | dict[str, Any]
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
    return str(exc) or type(exc).__name__


def _safe_mcp_exception(exc: BaseException) -> str:
    """返回可写入日志和清单的脱敏 MCP 错误摘要。"""

    return redact_mcp_sensitive_text(_format_mcp_exception(exc), os.environ)


def build_custom_tools(settings: Settings) -> list[ToolDefinition]:
    """返回本项目领域工具。"""

    return [internet_search]


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
) -> tuple[list[ToolDefinition], dict[str, tuple[str, ...]], dict[str, str]]:
    """发现 MCP 工具，同时记录实际暴露给 Agent 的工具清单。"""

    client = MultiServerMCPClient(servers)
    discovered_by_server = await asyncio.gather(
        *(
            client.get_tools(server_name=server_name)
            for server_name in servers
        ),
        return_exceptions=True,
    )
    tools: list[ToolDefinition] = []
    catalog: dict[str, tuple[str, ...]] = {}
    failures: dict[str, str] = {}
    for server_name, discovered in zip(
        servers,
        discovered_by_server,
        strict=True,
    ):
        if isinstance(discovered, BaseException):
            if not isinstance(discovered, Exception):
                raise discovered
            failures[server_name] = _safe_mcp_exception(discovered)
            continue

        allowlist = (tool_allowlists or {}).get(server_name)
        try:
            if allowlist is None:
                tools.extend(discovered)
                catalog[server_name] = tuple(tool.name for tool in discovered)
                continue

            by_name = {tool.name: tool for tool in discovered}
            missing = [name for name in allowlist if name not in by_name]
            if missing:
                raise RuntimeError(
                    "白名单工具不存在："
                    f"{', '.join(missing)}。"
                )
            tools.extend(by_name[name] for name in allowlist)
            catalog[server_name] = allowlist
        except Exception as exc:  # noqa: BLE001 - 隔离单个 MCP 服务的装配失败
            failures[server_name] = _safe_mcp_exception(exc)
    return tools, catalog, failures


async def load_mcp_tools(
    servers: dict[str, dict[str, Any]],
    tool_allowlists: dict[str, tuple[str, ...]] | None = None,
) -> list[ToolDefinition]:
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


async def build_agent_tools(settings: Settings) -> list[ToolDefinition]:
    """异步组合自定义 callable 和可选 MCP 工具。"""

    tools: list[ToolDefinition] = build_custom_tools(settings)
    if not settings.mcp_servers:
        tools.append(build_mcp_catalog_tool({}, {}))
        return tools

    mcp_tools, catalog, failures = await _load_mcp_tools_with_catalog(
        settings.mcp_servers,
        settings.mcp_tool_allowlists,
    )

    tools.extend(mcp_tools)
    if failures:
        failed_text = "、".join(
            f"{name}（{detail}）" for name, detail in sorted(failures.items())
        )
        print(f"MCP 服务部分加载失败，已跳过：{failed_text}")
    tools.append(build_mcp_catalog_tool(settings.mcp_servers, catalog, failures))
    return tools
