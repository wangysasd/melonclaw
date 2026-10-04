"""统一处理 LangChain 消息内容，避免依赖具体消息格式。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def display_blocks(content: Any) -> list[dict[str, str]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, Iterable) or isinstance(content, (bytes, dict)):
        return []
    result = []
    for block in content:
        if isinstance(block, str):
            result.append({"type": "text", "text": block})
        elif isinstance(block, dict):
            kind = block.get("type")
            value = block.get({"text": "text", "reasoning": "reasoning", "thinking": "thinking"}.get(kind, ""))
            if kind == "reasoning" and not isinstance(value, str):
                value = "".join(part.get("text", "") for part in block.get("summary", []) if isinstance(part, dict))
            if isinstance(value, str) and value:
                result.append({"type": "text" if kind == "text" else "reasoning", "text": value})
    return result


def answer_text(content: Any) -> str:
    return "".join(block["text"] for block in display_blocks(content) if block["type"] == "text")


def content_to_text(content: Any) -> str:
    """把字符串或 content blocks 转成可打印文本。"""

    return "".join(block["text"] for block in display_blocks(content))
