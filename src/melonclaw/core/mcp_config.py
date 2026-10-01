"""Deep Agents 应用使用的 MCP 服务配置加载与敏感信息脱敏。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MCP_CONFIG_PATH = PROJECT_ROOT / "mcp.json"
TRANSPORT_ALIASES = {
    "http": "http",
    "streamable_http": "http",
    "streamable-http": "http",
    "sse": "sse",
    "stdio": "stdio",
}

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_TOKEN_URL_SEGMENT = re.compile(r"(?i)([?&/]token=)[^&\s'\"<>]+")


def expand_env_placeholders(
    value: Any,
    environ: Mapping[str, str] | None = None,
) -> Any:
    """递归展开 MCP 配置中的 ``${VARIABLE_NAME}``。"""

    source = os.environ if environ is None else environ

    if isinstance(value, dict):
        return {
            key: expand_env_placeholders(item, source)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [expand_env_placeholders(item, source) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        variable_name = match.group(1)
        resolved = source.get(variable_name, "")
        if not resolved:
            raise RuntimeError(f"缺少 MCP 配置环境变量：{variable_name}。")
        return resolved

    return _ENV_PLACEHOLDER.sub(replace, value)


def load_builtin_mcp_seed(
    config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """读取仓库 ``mcp.json`` 作为内置 MCP 种子，不展开环境变量占位符。

    占位符原样存进数据库，运行时装配由 services/mcp.py 按 scope 决定
    展开所用的环境（global 用进程环境，user 用空环境直接拒绝）。
    """

    catalog = _load_server_catalog(config_path)
    if not catalog:
        return {}
    return _validate_servers(catalog)


def row_to_client_config(row: Mapping[str, Any]) -> dict[str, Any]:
    """把 mcp_servers 表行转换为 MultiServerMCPClient 配置。

    不做环境变量展开——展开时机和可用环境由调用方按 scope 决定。
    """

    transport = str(row["transport"])
    config: dict[str, Any] = {"transport": transport}
    if transport == "stdio":
        config["command"] = row["command"]
        args = row["args"]
        if args:
            config["args"] = list(args)
    else:
        config["url"] = row["url"]
    env = row["env"]
    if env:
        config["env"] = dict(env)
    headers = row["headers"]
    if headers:
        config["headers"] = dict(headers)
    return config


def redact_mcp_sensitive_text(
    value: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """隐藏 URL token 和当前 Tushare MCP 凭据。"""

    source = os.environ if environ is None else environ
    sanitized = _TOKEN_URL_SEGMENT.sub(r"\1<redacted>", value)
    token = source.get("TUSHARE_MCP_TOKEN", "")
    if token:
        sanitized = sanitized.replace(token, "<redacted>")
    return sanitized


def _load_server_catalog(
    config_path: str | Path | None,
) -> dict[str, dict[str, Any]]:
    path = Path(config_path) if config_path else DEFAULT_MCP_CONFIG_PATH
    try:
        raw_config = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}

    if _is_disabled_config(raw_config):
        return {}

    try:
        decoded = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MCP 配置文件不是合法 JSON：{path}") from exc

    if not isinstance(decoded, dict):
        raise RuntimeError(f"MCP 配置文件必须是 JSON 对象：{path}")
    if not decoded:
        return {}

    if "mcpServers" not in decoded:
        raise RuntimeError(f"MCP 配置文件必须包含 mcpServers 对象：{path}")

    servers = decoded["mcpServers"]
    if not isinstance(servers, dict):
        raise RuntimeError(f"MCP 配置文件的 mcpServers 必须是对象：{path}")
    return servers


def _is_disabled_config(raw_config: str) -> bool:
    """识别空配置或被整段 ``//`` 注释掉的 mcp.json。"""

    lines = [line.strip() for line in raw_config.splitlines()]
    return not lines or all(not line or line.startswith("//") for line in lines)


def _normalize_transport(
    server_name: str,
    server_config: Mapping[str, Any],
) -> str:
    """把通用配置中的 ``type`` 规范化为 LangChain 使用的 transport。"""

    declared_type = server_config.get("type")
    declared_transport = server_config.get("transport")
    if declared_type is not None and declared_transport is not None:
        normalized_type = (
            TRANSPORT_ALIASES.get(declared_type)
            if isinstance(declared_type, str)
            else None
        )
        normalized_transport = (
            TRANSPORT_ALIASES.get(declared_transport)
            if isinstance(declared_transport, str)
            else None
        )
        if normalized_type != normalized_transport:
            raise RuntimeError(
                f"MCP 服务 {server_name!r} 的 type 与 transport 冲突。"
            )

    declared = declared_transport if declared_transport is not None else declared_type
    if declared is None:
        has_command = isinstance(server_config.get("command"), str)
        has_url = isinstance(server_config.get("url"), str)
        if has_command == has_url:
            raise RuntimeError(
                f"MCP 服务 {server_name!r} 必须提供 type/transport，"
                "或通过 command/url 明确连接方式。"
            )
        return "stdio" if has_command else "http"

    if not isinstance(declared, str) or declared not in TRANSPORT_ALIASES:
        raise RuntimeError(
            f"MCP 服务 {server_name!r} 的 type/transport 不受支持：{declared!r}。"
        )
    return TRANSPORT_ALIASES[declared]


def _validate_servers(
    decoded: Any,
) -> dict[str, dict[str, Any]]:
    if not isinstance(decoded, dict):
        raise RuntimeError("MCP 服务配置必须是对象，键为服务名。")

    servers: dict[str, dict[str, Any]] = {}
    for server_name, server_config in decoded.items():
        if not isinstance(server_name, str) or not server_name.strip():
            raise RuntimeError("MCP 服务名必须是非空字符串。")
        if not isinstance(server_config, dict):
            raise RuntimeError(f"MCP 服务 {server_name!r} 的配置必须是对象。")

        transport = _normalize_transport(server_name, server_config)
        normalized_config = dict(server_config)
        normalized_config.pop("type", None)
        normalized_config["transport"] = transport

        if transport == "stdio":
            if not isinstance(normalized_config.get("command"), str):
                raise RuntimeError(f"STDIO MCP 服务 {server_name!r} 必须提供 command。")
            if not isinstance(normalized_config.get("args", []), list):
                raise RuntimeError(f"STDIO MCP 服务 {server_name!r} 的 args 必须是数组。")
        else:
            url = normalized_config.get("url")
            parsed = urlparse(url) if isinstance(url, str) else None
            if (
                parsed is None
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
            ):
                raise RuntimeError(
                    f"HTTP/SSE MCP 服务 {server_name!r} 必须提供 http(s) url。"
                )

        servers[server_name] = normalized_config

    return servers


def select_mcp_tool_names(names, allowlist):
    """白名单只取实际目录交集，缺失项不扩权也不阻断其他工具。"""
    available = set(names)
    if allowlist is None:
        return list(dict.fromkeys(names)), []
    return (
        [name for name in allowlist if name in available],
        [name for name in allowlist if name not in available],
    )


def redact_mcp_connection_text(value: str, config: Mapping[str, Any]) -> str:
    """描述和工具结果共同隐藏当前连接的 Headers/Env 凭据。"""
    for secret in [*config.get("headers", {}).values(), *config.get("env", {}).values()]:
        if secret:
            value = value.replace(secret, "<redacted>")
            if secret.startswith("Bearer "):
                value = value.replace(secret[7:], "<redacted>")
    return redact_mcp_sensitive_text(value)
