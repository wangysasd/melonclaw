"""统一处理 LangChain 消息内容，避免 CLI 依赖具体消息格式。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def content_to_text(content: Any) -> str:
    """把字符串或 content blocks 转成可打印文本。"""

    if isinstance(content, str):
        return content

    if not isinstance(content, Iterable) or isinstance(content, (bytes, dict)):
        return ""

    chunks: list[str] = []
    for block in content:
        if isinstance(block, str):
            chunks.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


def final_message_text(result: dict[str, Any]) -> str:
    """取 invoke 结果中的最后一条消息文本。"""

    messages = result.get("messages", [])
    if not messages:
        return ""
    return content_to_text(getattr(messages[-1], "content", ""))
