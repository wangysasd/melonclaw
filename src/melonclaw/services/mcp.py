"""MCP 运行时装配：数据库行 → MultiServerMCPClient 配置。

这是 Agent 运行时拿到 MCP 服务的唯一入口（内置 mcp.json 只作为 db-init
种子）。安全边界：

- ``scope='user'`` 的行仅允许 http/sse transport，且配置值里不允许出现
  ``${VAR}`` 占位符——保存时校验拒绝，装载时用空环境二次兜底，从机制上
  阻断用户 MCP 引用应用自身密钥。
- ``scope='global'`` 的行允许 stdio 和 ``${VAR}`` 占位符（展开用进程环境），
  因为发布 global 配置需要管理员权限。
"""

from __future__ import annotations

import os
import re
from typing import Any

from melonclaw.core.mcp_config import expand_env_placeholders, row_to_client_config

USER_SCOPE_TRANSPORTS = ("http", "sse")
MCP_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class McpConfigError(ValueError):
    """用户提交的 MCP 配置不合法。"""

    status_code = 422


def validate_mcp_payload(
    *,
    scope: str,
    transport: str,
    url: str | None,
    command: str | None,
    env: dict[str, str] | None,
    headers: dict[str, str] | None,
) -> None:
    """保存前校验 MCP 配置；违反安全边界的直接抛 McpConfigError。"""

    if transport not in ("http", "sse", "stdio"):
        raise McpConfigError(f"不支持的 transport：{transport!r}。")
    if scope == "user" and transport not in USER_SCOPE_TRANSPORTS:
        raise McpConfigError("用户级 MCP 仅支持 http/sse transport。")
    if transport == "stdio":
        if scope != "global":
            raise McpConfigError("stdio MCP 只允许管理员发布的全局配置。")
        if not command:
            raise McpConfigError("stdio MCP 必须提供 command。")
    else:
        if not url:
            raise McpConfigError("http/sse MCP 必须提供 url。")
    for label, values in (("env", env), ("headers", headers)):
        for key, value in (values or {}).items():
            if "${" in str(key) or "${" in str(value):
                raise McpConfigError(
                    f"用户级配置的 {label} 不允许使用 ${{VAR}} 占位符：{key!r}。"
                )
    if url and scope == "user" and "${" in url:
        raise McpConfigError("用户级配置的 url 不允许使用 ${VAR} 占位符。")


def resolve_user_mcp_servers(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, ...]]]:
    """把可见的 mcp_servers 表行装配为 client 配置和工具白名单。

    ``rows`` 由仓储层按可见性过滤（global + 创建者自己的 user scope），
    这里负责按 scope 做环境变量展开并生成白名单。
    """

    servers: dict[str, dict[str, Any]] = {}
    allowlists: dict[str, tuple[str, ...]] = {}
    for row in rows:
        config = row_to_client_config(row)
        environ: dict[str, str] | Any = (
            os.environ if row["scope"] == "global" else {}
        )
        servers[str(row["slug"])] = expand_env_placeholders(config, environ)
        raw_allowlist = row.get("tool_allowlist")
        if raw_allowlist:
            allowlists[str(row["slug"])] = tuple(str(name) for name in raw_allowlist)
    return servers, allowlists
