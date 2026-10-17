"""仅变换监控副本：结构化凭据、常见文本秘密及个人信息的有界脱敏。"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from melonclaw.core.mcp_config import redact_mcp_sensitive_text

_SECRET_FIELD = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization|cookie|headers|"
    r"database_url|connection_string|model_spec|runtime|configurable)"
)
_SECRET_TEXT = re.compile(
    r'''(?ix)(\b[A-Z0-9_-]{0,80}(?:api[_-]?key|token|secret|password|authorization|cookie)[A-Z0-9_-]{0,80}
    ["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]]+)'''
)
_AUTH_TOKEN = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_TOKEN = re.compile(r"\b(?:sk|pk|tvly)-[A-Za-z0-9_-]{8,}\b")
_CREDENTIAL_URL = re.compile(r"\b[a-zA-Z][\w+.-]*://[^\s/\"']+:[^\s/\"']+@[^\s\"']+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_MEDIA = re.compile(r"data:[^\s\"']+", re.IGNORECASE)


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = redact_mcp_sensitive_text(text)
    text = _AUTH_TOKEN.sub("[REDACTED]", text)
    text = _SECRET_TEXT.sub(r"\1[REDACTED]", text)
    for pattern in (_TOKEN, _CREDENTIAL_URL, _EMAIL, _PHONE, _MEDIA):
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_data(data: Any, secrets: tuple[str, ...] = (), depth: int = 0) -> Any:
    """不调用任意对象 repr；模型/运行上下文对象不进入监控。"""
    if depth > 20:
        return "[DEPTH LIMIT]"
    if isinstance(data, BaseMessage):
        message = {"role": {"human": "user", "ai": "assistant"}.get(data.type, data.type),
                   "content": data.content, "name": data.name,
                   "tool_call_id": getattr(data, "tool_call_id", None)}
        if isinstance(data, AIMessage):
            message["tool_calls"] = data.tool_calls
        return redact_data(message, secrets, depth + 1)
    if isinstance(data, dict):
        result = {}
        for key, value in list(data.items())[:1000]:
            if type(key) not in {str, int, float, bool, type(None)}:
                result["[OBJECT KEY OMITTED]"] = "[OBJECT OMITTED]"
                continue
            name = str(key)
            result[redact_text(name, secrets)] = (
                "[REDACTED]" if _SECRET_FIELD.search(name)
                or name in {"image_url", "file_data", "base64", "audio"}
                or name == "data" and type(data.get("type")) is str
                and data["type"] in {"base64", "image", "audio", "file", "input_image", "input_audio"}
                else redact_data(value, secrets, depth + 1)
            )
        return result
    if isinstance(data, (list, tuple)):
        return [redact_data(value, secrets, depth + 1) for value in data[:1000]]
    if isinstance(data, str):
        # Some tool arguments/results are JSON strings inside a message.
        if data.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(redact_data(json.loads(data), secrets, depth + 1), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        value = redact_text(data, secrets)
        return value[:100_000] + "[TRUNCATED]" if len(value) > 100_000 else value
    if data is None or isinstance(data, (bool, int, float)):
        return data
    return "[OBJECT OMITTED]"


def make_export_mask(*, capture_content: bool, secrets: tuple[str, ...]):
    """SDK v5 导出钩子，覆盖 input/output、metadata、模型参数和状态消息。"""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    def mask_otel_spans(*, params):
        patches = {}
        for identifier, span in params.spans.items():
            replacements = {}
            for key, value in span.attributes.items():
                if not capture_content and (
                    key.endswith((".input", ".output", ".model.parameters", ".status_message"))
                    or ".metadata." in key and key.rsplit(".", 1)[-1] not in {
                        "tenant_id", "run_id", "task_id", "project_id", "model_id",
                        "request_id", "assistant_message_id", "feature", "status", "error_code", "resuming",
                    }
                ):
                    replacements[key] = '"[CONTENT DISABLED]"'
                elif isinstance(value, str):
                    try:
                        decoded = json.loads(value)
                    except (ValueError, RecursionError):
                        replacements[key] = "[REDACTED]" if _SECRET_FIELD.search(key) else redact_text(value, secrets)
                    else:
                        replacements[key] = json.dumps(
                            "[REDACTED]" if _SECRET_FIELD.search(key) else redact_data(decoded, secrets),
                            ensure_ascii=False,
                        )
                elif isinstance(value, (tuple, list)):
                    replacements[key] = [redact_text(item, secrets) if isinstance(item, str) else item for item in value]
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
        return MaskOtelSpansResult(span_patches=patches)

    return mask_otel_spans
