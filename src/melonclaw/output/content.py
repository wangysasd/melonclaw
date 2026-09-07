"""统一处理 LangChain 消息内容，避免依赖具体消息格式。"""

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
