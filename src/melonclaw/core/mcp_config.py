"""Deep Agents 应用使用的 MCP 服务配置加载与敏感信息脱敏。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MCP_CONFIG_PATH = PROJECT_ROOT / "mcp.json"
DEFAULT_TUSHARE_SERVER_NAME = "tushare_mcp"
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


def load_agent_mcp_servers(
    environ: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """加载要注入 Deep Agent 的 MCP 服务。

    MCP 服务清单只来自根目录 ``mcp.json``。环境变量不负责启用服务，
    只用于展开配置文件中明确写出的凭据占位符。
    """

    source = os.environ if environ is None else environ
    catalog = _load_server_catalog(config_path)
    if not catalog:
        return {}

    expanded = expand_env_placeholders(catalog, source)
    return _validate_servers(expanded)


def load_mcp_tool_allowlists(
    mcp_server_names: Collection[str],
    environ: Mapping[str, str] | None = None,
) -> dict[str, tuple[str, ...]]:
    """返回每个 MCP 服务要暴露给模型的工具白名单。

    Tushare 默认不设置白名单，即向模型暴露 Server 返回的全部工具。只有显式
    配置 ``DEEPAGENTS_TUSHARE_MCP_TOOLS`` 时，才按逗号分隔的名称缩小范围。
    """

    if DEFAULT_TUSHARE_SERVER_NAME not in mcp_server_names:
        return {}

    source = os.environ if environ is None else environ
    raw_names = source.get("DEEPAGENTS_TUSHARE_MCP_TOOLS")
    if raw_names is None or not raw_names.strip() or raw_names.strip() == "*":
        return {}

    names = tuple(name.strip() for name in raw_names.split(",") if name.strip())

    if len(names) != len(set(names)):
        raise RuntimeError("DEEPAGENTS_TUSHARE_MCP_TOOLS 中不能包含重复工具名。")
    return {DEFAULT_TUSHARE_SERVER_NAME: names}


def _load_server_catalog(
    config_path: str | Path | None,
) -> dict[str, dict[str, Any]]:
    path = Path(config_path) if config_path else DEFAULT_MCP_CONFIG_PATH
    try:
        raw_config = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到 MCP 配置文件：{path}") from exc

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

    present_keys = [key for key in ("mcpServers", "servers") if key in decoded]
    if len(present_keys) > 1:
        raise RuntimeError(
            f"MCP 配置文件不能同时包含 mcpServers 和 servers：{path}"
        )
    if not present_keys:
        raise RuntimeError(
            f"MCP 配置文件必须包含 mcpServers 对象（兼容旧的 servers）：{path}"
        )

    servers = decoded[present_keys[0]]
    if not isinstance(servers, dict):
        raise RuntimeError(f"MCP 配置文件的 {present_keys[0]} 必须是对象：{path}")
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
