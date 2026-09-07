"""自定义工具与可选 MCP 工具的装配。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from melonclaw.core.config import Settings
from melonclaw.core.mcp_config import redact_mcp_sensitive_text
from melonclaw.tool.search import internet_search


ToolDefinition = Callable[..., Any] | dict[str, Any]


def build_custom_tools(settings: Settings) -> list[ToolDefinition]:
    """返回本项目领域工具。"""

    return [internet_search]


async def load_mcp_tools(
    servers: dict[str, dict[str, Any]],
    tool_allowlists: dict[str, tuple[str, ...]] | None = None,
) -> list[ToolDefinition]:
    """按官方 MCP 适配器模式加载多个服务暴露的工具。"""

    if not servers:
        return []

    try:
        client = MultiServerMCPClient(servers)
        discovered_by_server = await asyncio.gather(
            *(
                client.get_tools(server_name=server_name)
                for server_name in servers
            )
        )
        tools: list[ToolDefinition] = []
        for server_name, discovered in zip(
            servers,
            discovered_by_server,
            strict=True,
        ):
            allowlist = (tool_allowlists or {}).get(server_name)
            if allowlist is None:
                tools.extend(discovered)
                continue

            by_name = {tool.name: tool for tool in discovered}
            missing = [name for name in allowlist if name not in by_name]
            if missing:
                raise RuntimeError(
                    f"MCP 服务 {server_name!r} 没有暴露白名单工具："
                    f"{', '.join(missing)}。"
                )
            tools.extend(by_name[name] for name in allowlist)
        return tools
    except Exception as exc:
        server_names = ", ".join(sorted(servers))
        safe_detail = redact_mcp_sensitive_text(str(exc), os.environ)
        raise RuntimeError(
            f"加载 MCP 工具失败（服务：{server_names}）："
            f"{type(exc).__name__}: {safe_detail}"
        ) from None


async def build_agent_tools(settings: Settings) -> list[ToolDefinition]:
    """异步组合自定义 callable 和可选 MCP 工具。"""

    tools: list[ToolDefinition] = build_custom_tools(settings)
    tools.extend(
        await load_mcp_tools(
            settings.mcp_servers,
            settings.mcp_tool_allowlists,
        )
    )
    return tools
