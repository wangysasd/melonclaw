"""聊天 MCP JSON 提取；原始配置只交给草稿仓储，不进入模型。"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from melonclaw.core.mcp_config import TRANSPORT_ALIASES
from melonclaw.services.mcp import MCP_SLUG_RE, McpConfigError, validate_mcp_payload

MARKER = re.compile(r'"(?:mcpServers|url|command|headers)"\s*:')
FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result or key in {"__proto__", "constructor", "prototype"}:
            raise McpConfigError("MCP JSON 含重复或不允许的键。")
        result[key] = value
    return result


def _json_object_spans(content):
    """定位文本中的完整顶层对象，忽略 JSON 字符串内的大括号。"""
    depth, start, quoted, escaped = 0, 0, False, False
    for index, char in enumerate(content):
        if depth and quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if depth and char == '"':
            quoted = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif depth and char == "}":
            depth -= 1
            if depth == 0:
                yield start, index + 1
    if depth and MARKER.search(content[start:]):
        raise McpConfigError("MCP JSON 不完整，请检查大括号和字符串。")


def parse_chat_mcp(content: str, *, user_id: str, conversation_id: str, request_id: str):
    """返回脱敏消息和草稿，允许用户在 JSON 前后说明安装意图。"""
    matches = [match for match in FENCE.finditer(content) if MARKER.search(match[1])]
    if len(matches) > 1:
        raise McpConfigError("请将 MCP 配置放在同一个 JSON 代码块中。")
    if matches:
        match = matches[0]
        raw = match[1].strip()
        before, after = content[:match.start()], content[match.end():]
        if any('"mcpServers"' in part or ('"headers"' in part and MARKER.search(part)) for part in (before, after)):
            raise McpConfigError("请把全部 MCP 配置放在同一个 JSON 代码块中。")
    else:
        spans = [(start, end) for start, end in _json_object_spans(content)
                 if MARKER.search(content[start:end])]
        if len(spans) > 1:
            raise McpConfigError("请把多个服务合并到同一个 mcpServers JSON 对象。")
        if not spans:
            if '"mcpServers"' in content or ('"headers"' in content and MARKER.search(content)):
                raise McpConfigError("请粘贴完整 MCP JSON，或放入一个 JSON 代码块后说明安装意图。")
            return content, []
        start, end = spans[0]
        raw, before, after = content[start:end], content[:start], content[end:]
        if any('"mcpServers"' in part or ('"headers"' in part and MARKER.search(part)) for part in (before, after)):
            raise McpConfigError("请把全部 MCP 配置合并到同一个 JSON 对象。")
    try:
        if len(raw.encode()) > 65536:
            raise McpConfigError("MCP JSON 不能超过 64 KiB。")
        decoded = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(decoded, dict):
            raise McpConfigError("MCP JSON 必须是对象。")
        pending = [(decoded, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > 16:
                raise McpConfigError("MCP JSON 嵌套不能超过 16 层。")
            children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
            pending.extend((child, depth + 1) for child in children)
        if not {"mcpServers", "url", "command", "headers"} & decoded.keys():
            return content, []
        if "mcpServers" in decoded:
            if set(decoded) != {"mcpServers"} or not isinstance(decoded["mcpServers"], dict):
                raise McpConfigError("mcpServers 必须是唯一的顶层对象。")
            entries = list(decoded["mcpServers"].items())
        else:
            entries = [("chat-mcp", decoded)]
        if not 1 <= len(entries) <= 8:
            raise McpConfigError("每条消息支持 1–8 个 MCP 服务。")
        drafts = []
        for name, config in entries:
            payload = normalize_chat_mcp(name, config)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            identifier = str(uuid5(NAMESPACE_URL, f"mcp:{user_id}:{conversation_id}:{request_id}:{name}:{digest}"))
            drafts.append({"id": identifier, "payload": payload})
        references = "\n".join(
            f"MCP 配置草稿：{draft['id']}（{draft['payload']['slug']}，仅自己；凭据已隔离）"
            for draft in drafts
        )
        # 原始 JSON 与非配置文本彻底分离；只保留用户明确写在块外的意图。
        return "\n".join(part.strip() for part in (before, references, after) if part.strip()), drafts
    except McpConfigError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise McpConfigError("MCP JSON 格式或字段无效，请检查配置；原文未发送给模型。") from None


def normalize_chat_mcp(name, config):
    if not isinstance(name, str) or not MCP_SLUG_RE.fullmatch(name):
        raise McpConfigError("MCP 标识需为 1–64 个小写字母、数字、下划线或短横线。")
    if not isinstance(config, dict) or set(config) - {"type", "transport", "url", "command", "args", "headers", "env"}:
        raise McpConfigError("MCP 配置含不支持的字段；归属与启用由系统决定。")
    types = [config[key] for key in ("type", "transport") if key in config]
    if any(not isinstance(value, str) or value not in TRANSPORT_ALIASES for value in types):
        raise McpConfigError("MCP 连接类型无效。")
    if len({TRANSPORT_ALIASES[value] for value in types}) > 1:
        raise McpConfigError("type 和 transport 冲突。")
    transport = TRANSPORT_ALIASES[types[0]] if types else ("stdio" if config.get("command") else "http")
    for key in ("url", "command"):
        if key in config and (not isinstance(config[key], str) or len(config[key]) > 2000):
            raise McpConfigError("MCP 地址或启动程序无效。")
    args = config.get("args", [])
    if not isinstance(args, list) or len(args) > 64 or any(not isinstance(v, str) or len(v) > 8192 for v in args):
        raise McpConfigError("MCP args 必须为字符串数组。")
    maps = {}
    for key in ("headers", "env"):
        value = config.get(key, {})
        if not isinstance(value, dict) or len(value) > 64 or any(
            not isinstance(v, str) or len(k) > 240 or len(v) > 8192 for k, v in value.items()
        ):
            raise McpConfigError("MCP headers/env 必须为受限字符串键值对象。")
        maps[key] = value
    payload = dict(slug=name, display_name=name, description="聊天安装", transport=transport,
                   url=config.get("url"), command=config.get("command"), args=args,
                   **maps, tool_allowlist=None)
    validate_mcp_payload(scope="user", **{k: payload[k] for k in ("transport", "url", "command", "args", "headers", "env")})
    return payload


def connection_preview(payload):
    """不回传可能在路径、query 或 fragment 中的 Token。"""
    parsed = urlsplit(payload["url"])
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    # 非法端口也不回显原文。
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        raise McpConfigError("MCP 端口无效。") from None
    return f"{parsed.scheme}://{host}{port}/…"
