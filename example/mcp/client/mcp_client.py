"""从 mcp.json 加载 MCP 服务并发现/调用工具。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "mcp.json"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
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
    """递归展开配置中的 ``${VARIABLE_NAME}`` 环境变量占位符。"""

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


def redact_sensitive_text(
    value: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """隐藏异常文本中的 URL token 和当前 Tushare MCP 凭据。"""

    source = os.environ if environ is None else environ
    sanitized = _TOKEN_URL_SEGMENT.sub(r"\1<redacted>", value)
    token = source.get("TUSHARE_MCP_TOKEN", "")
    if token:
        sanitized = sanitized.replace(token, "<redacted>")
    return sanitized


def load_mcp_config(
    config_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """读取并校验 mcp.json 中的服务配置。"""

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    load_dotenv(DEFAULT_ENV_PATH)
    raw_config = expand_env_placeholders(
        json.loads(path.read_text(encoding="utf-8"))
    )

    if not isinstance(raw_config, dict):
        raise ValueError("mcp.json 必须是 JSON 对象。")

    present_keys = [key for key in ("mcpServers", "servers") if key in raw_config]
    if len(present_keys) > 1:
        raise ValueError("mcp.json 不能同时包含 mcpServers 和 servers。")
    if not present_keys:
        raise ValueError("mcp.json 必须包含 mcpServers 对象（兼容旧的 servers）。")

    servers = raw_config[present_keys[0]]
    if not isinstance(servers, dict) or not servers:
        raise ValueError(f"mcp.json 的 {present_keys[0]} 必须是非空对象。")

    validated: dict[str, dict[str, Any]] = {}
    for name, cfg in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("MCP 服务名必须是非空字符串。")
        if not isinstance(cfg, dict):
            raise ValueError(f"MCP 服务 {name!r} 的配置必须是对象。")

        declared_type = cfg.get("type")
        declared_transport = cfg.get("transport")
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
                raise ValueError(f"MCP 服务 {name!r} 的 type 与 transport 冲突。")

        declared = (
            declared_transport if declared_transport is not None else declared_type
        )
        if declared is None:
            has_command = isinstance(cfg.get("command"), str)
            has_url = isinstance(cfg.get("url"), str)
            if has_command == has_url:
                raise ValueError(
                    f"MCP 服务 {name!r} 必须提供 type/transport，"
                    "或通过 command/url 明确连接方式。"
                )
            transport = "stdio" if has_command else "http"
        elif not isinstance(declared, str) or declared not in TRANSPORT_ALIASES:
            raise ValueError(
                f"MCP 服务 {name!r} 的 type/transport 不受支持：{declared!r}。"
            )
        else:
            transport = TRANSPORT_ALIASES[declared]

        normalized = dict(cfg)
        normalized.pop("type", None)
        normalized["transport"] = transport
        if transport == "stdio":
            if not isinstance(normalized.get("command"), str):
                raise ValueError(f"STDIO 服务 {name!r} 必须提供 command。")
            if not isinstance(normalized.get("args", []), list):
                raise ValueError(f"STDIO 服务 {name!r} 的 args 必须是数组。")
        else:
            url = normalized.get("url")
            parsed = urlparse(url) if isinstance(url, str) else None
            if (
                parsed is None
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
            ):
                raise ValueError(f"HTTP/SSE 服务 {name!r} 必须提供 http(s) url。")

        validated[name] = normalized

    return validated


def build_mcp_client(
    config_path: str | Path | None = None,
) -> MultiServerMCPClient:
    """根据 mcp.json 创建包含全部已配置服务的 Client。"""

    servers = load_mcp_config(config_path)
    return MultiServerMCPClient(servers)


async def load_server_tools(
    server_name: str,
    config_path: str | Path | None = None,
) -> list[Any]:
    """从包含全部配置的 Client 中发现目标 Server 暴露的工具。"""

    client = build_mcp_client(config_path)
    return list(await client.get_tools(server_name=server_name))


async def invoke_tool(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    config_path: str | Path | None = None,
) -> Any:
    """发现并调用目标 MCP 工具。"""

    for tool in await load_server_tools(server_name, config_path):
        if tool.name == tool_name:
            return await tool.ainvoke(arguments)
    raise ValueError(f"未知 MCP 工具 {tool_name!r}")


def _parse_arguments(raw: str) -> dict[str, Any]:
    """把 --arguments 字符串解析为 dict。"""
    arguments = json.loads(raw)
    if not isinstance(arguments, dict):
        raise ValueError("--arguments 必须解析为 JSON 对象。")
    return arguments


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取 mcp.json 使用 MCP Client")
    parser.add_argument("--server", default="math_stdio", help="mcp.json 中的服务名。")
    parser.add_argument("--config", type=Path, default=None, help="自定义 mcp.json 路径。")
    parser.add_argument("--list-tools", action="store_true", help="列出服务工具。")
    parser.add_argument("--tool", help="要调用的工具名。")
    parser.add_argument(
        "--arguments",
        default="{}",
        help='工具参数 JSON，例如 \'{"a": 2, "b": 3}\'。',
    )
    return parser


async def run_client(args: argparse.Namespace) -> None:
    if args.tool:
        result = await invoke_tool(
            args.server, args.tool, _parse_arguments(args.arguments), args.config,
        )
        if isinstance(result, str):
            print(result)
        else:
            print(json.dumps(result, ensure_ascii=False, default=str))
        return

    tools = await load_server_tools(args.server, args.config)
    # 默认行为是发现工具；--list-tools 让意图更明确，但不是必需参数。
    if args.list_tools or not args.tool:
        for tool in tools:
            print(f"{tool.name}: {tool.description or '(无描述)'}")


def main() -> int:
    args = build_parser().parse_args()
    try:
        asyncio.run(run_client(args))
    except Exception:
        formatted = "".join(traceback.format_exception(*sys.exc_info()))
        print(redact_sensitive_text(formatted), file=sys.stderr, end="")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
