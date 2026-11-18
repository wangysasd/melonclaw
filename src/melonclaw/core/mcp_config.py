"""Deep Agents 应用使用的 MCP 服务配置转换与敏感信息脱敏。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

# 当前接受的连接类型。别名只在聊天配置解析时折成这些值，不能单独放行。
MCP_TRANSPORTS = frozenset({"http", "sse", "stdio"})
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
    url = str(config.get("url", ""))
    parsed = urlparse(url)
    # URL 中也可能携带认证信息；完整 URL、query、fragment 和非标准路径片段不进入工具文本。
    url_secrets = [url, parsed.fragment, *(value for _, value in parse_qsl(parsed.query))]
    url_secrets.extend(unquote(part) for part in parsed.path.split("/")
                       if part and part.lower() not in {"mcp", "sse", "messages", "api", "v1", "v2"})
    for secret in [*config.get("headers", {}).values(), *config.get("env", {}).values(), *url_secrets]:
        if secret:
            value = value.replace(secret, "<redacted>")
            if secret.startswith("Bearer "):
                value = value.replace(secret[7:], "<redacted>")
    return redact_mcp_sensitive_text(value)
