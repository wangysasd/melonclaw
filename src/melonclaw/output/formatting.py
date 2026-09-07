"""Web 流式事件使用的文本格式化与敏感信息脱敏工具。"""

from __future__ import annotations

import json
import re
from typing import Any

from melonclaw.core.mcp_config import redact_mcp_sensitive_text

_SECRET_QUOTED_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>\b(?:DEEPSEEK_API_KEY|TUSHARE_MCP_TOKEN|TAVILY_API_KEY|OPENAI_API_KEY|"
    r"ANTHROPIC_API_KEY|LANGSMITH_API_KEY|api[_-]?key|access[_-]?token|"
    r"secret|password|authorization)\b)"
    r"(?P<key_quote>[\"']?)(?P<separator>\s*[:=]\s*)"
    r"(?P<value_quote>[\"'])(?P<value>.*?)(?P=value_quote)"
)
_AUTHORIZATION_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>\bauthorization\b)(?P<key_quote>[\"']?)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[^\"'\s\r\n][^\r\n,;}\]]*)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>\b(?:DEEPSEEK_API_KEY|TUSHARE_MCP_TOKEN|TAVILY_API_KEY|OPENAI_API_KEY|"
    r"ANTHROPIC_API_KEY|LANGSMITH_API_KEY|api[_-]?key|access[_-]?token|"
    r"secret|password)\b)(?P<key_quote>[\"']?)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[^\"'\s,;}\]]+)"
)
_TOKEN_PATTERN = re.compile(r"\b(?:sk|tvly)-[A-Za-z0-9_-]{8,}\b")


def sanitize_text(value: str) -> str:
    """隐藏常见环境变量、Token 和授权字段的值。"""

    value = redact_mcp_sensitive_text(value)
    value = _SECRET_QUOTED_ASSIGNMENT.sub(
        r"\g<key>\g<key_quote>\g<separator>\g<value_quote>"
        r"<redacted>\g<value_quote>",
        value,
    )
    value = _AUTHORIZATION_ASSIGNMENT.sub(
        r"\g<key>\g<key_quote>\g<separator><redacted>",
        value,
    )
    value = _SECRET_ASSIGNMENT.sub(
        r"\g<key>\g<key_quote>\g<separator><redacted>",
        value,
    )
    return _TOKEN_PATTERN.sub("<redacted-token>", value)


def _preview(value: Any, limit: int = 4000) -> str:
    """把工具参数或结果转成有限长度的可读文本。"""

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = sanitize_text(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...（已截断，原始长度 {len(text)}）"


def _decode_tool_args(value: Any) -> Any:
    """将流式工具调用中逐片累积的 JSON 参数尽量还原成对象。"""

    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _call_key(
    call: dict[str, Any],
    index: int,
    index_keys: dict[int, str],
) -> str:
    """把后续只有 index 的参数分片关联回首次出现的调用 ID。"""

    call_index = call.get("index", index)
    if call.get("id"):
        key = f"id:{call['id']}"
        index_keys[call_index] = key
        return key
    return index_keys.get(call_index, f"index:{call_index}")
