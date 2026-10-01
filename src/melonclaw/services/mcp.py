"""MCP 配置校验、两层来源选择及运行时装配。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from urllib.parse import urlsplit

from melonclaw.core.mcp_config import expand_env_placeholders, row_to_client_config
from melonclaw.core.mcp_credentials import decode_credentials

MCP_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HEADER_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class McpConfigError(ValueError):
    status_code = 422


def validate_mcp_payload(*, scope, transport, url, command, env, headers, args=None):
    if transport not in ("http", "sse", "stdio"):
        raise McpConfigError("不支持的 MCP 连接类型。")
    if scope == "user" and transport == "stdio":
        raise McpConfigError("个人 MCP 仅支持 HTTP/SSE，stdio 仅限管理员。")
    if transport == "stdio":
        if not command or not command.strip() or url or headers:
            raise McpConfigError("stdio 必须填写启动程序，不能设置 URL 或请求头。")
    else:
        try:
            parsed = urlsplit(url or "")
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid = False
        if not valid or command or args or env:
            raise McpConfigError("HTTP/SSE 需要有效的 HTTP(S) 地址，不能含用户凭据或进程配置。")
    all_values = [
        url or "",
        command or "",
        *(args or []),
        *(env or {}).keys(),
        *(env or {}).values(),
        *(headers or {}).keys(),
        *(headers or {}).values(),
    ]
    if scope == "user" and any("${" in value for value in all_values):
        raise McpConfigError("个人 MCP 不允许引用服务器环境变量。")
    lowered = [key.lower() for key in (headers or {})]
    if len(set(lowered)) != len(lowered):
        raise McpConfigError("请求头名称重复（不区分大小写）。")
    if any(not HEADER_RE.fullmatch(key) for key in (headers or {})) or any(
        "\r" in value or "\n" in value for value in (headers or {}).values()
    ):
        raise McpConfigError("请求头名称或值无效。")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in (env or {})):
        raise McpConfigError("环境变量名称无效。")


def selected_mcp_rows(rows: list[dict]) -> list[dict]:
    selected = {}
    for row in rows:
        if row["slug"] not in selected or row["scope"] == "user":
            selected[row["slug"]] = row
    return [row for row in selected.values() if row["enabled"] and row["user_enabled"] is not False]


def mcp_snapshot_revision(rows: list[dict]) -> str:
    payload = sorted((str(row["id"]), row["version"], row["user_enabled"]) for row in rows)
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def resolved_mcp_config(row: dict) -> dict:
    config = row_to_client_config(
        {
            **row,
            "headers": decode_credentials(row["headers"]),
            "env": decode_credentials(row["env"]),
        }
    )
    return expand_env_placeholders(config, os.environ if row["scope"] == "global" else {})


def resolve_user_mcp_servers(rows: list[dict]) -> tuple[dict, dict]:
    servers, allowlists = {}, {}
    for row in selected_mcp_rows(rows):
        # Decryption/configuration failure belongs to this server, never a fallback source.
        try:
            servers[row["slug"]] = resolved_mcp_config(row)
        except (ValueError, RuntimeError):
            servers[row["slug"]] = {"transport": row["transport"], "configuration_error": True}
        if row["tool_allowlist"] is not None:
            allowlists[row["slug"]] = tuple(row["tool_allowlist"])
    return servers, allowlists
