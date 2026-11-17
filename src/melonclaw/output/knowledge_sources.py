"""在工具展示预览截断前提取完整、脱敏的知识库来源。"""

import json
import re
from typing import Any

from melonclaw.output.formatting import sanitize_text

SOURCE_TOOLS = frozenset({"search_knowledge", "open_knowledge_document"})
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SOURCE_URL = re.compile(
    rf"/api/knowledge-bases/{_UUID}/documents/{_UUID}/text"
    rf"\?revision_id={_UUID}&offset=[0-9]+&limit=4000"
)


def _cards(value: Any):
    update = getattr(value, "update", None)
    if isinstance(update, dict):
        yield from _cards(update)
    elif isinstance(value, dict):
        if isinstance(value.get("knowledge_sources"), list):
            yield from value["knowledge_sources"]
        else:
            for key in ("messages", "output", "content", "result", "text"):
                if key in value:
                    yield from _cards(value[key])
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _cards(item)
    elif isinstance(value, str):
        try:
            payload = json.loads(value)
        except ValueError:
            return
        if isinstance(payload, dict):
            yield from _cards(payload)
    elif getattr(value, "content", None) is not None:
        yield from _cards(value.content)


def knowledge_tool_metadata(name: str, value: Any) -> dict:
    """仅内置只读工具可发布来源；不从任意工具正文扫描或恢复链接。"""
    if name not in SOURCE_TOOLS:
        return {}
    sources, seen = [], set()
    for card in _cards(value):
        if not isinstance(card, dict):
            continue
        url = card.get("source_url")
        if not isinstance(url, str) or not _SOURCE_URL.fullmatch(url) or url in seen:
            continue
        seen.add(url)
        safe = {"source_url": url, "listed": card.get("listed") is not False}
        for key, limit in (("title", 40), ("heading", 40), ("warning", 1000)):
            if isinstance(card.get(key), str):
                safe[key] = sanitize_text(card[key])[:limit]
        for key, allowed in (
            ("kind", {"source_chunk"}),
        ):
            if isinstance(card.get(key), str) and card[key] in allowed:
                safe[key] = card[key]
        sources.append(safe)
    return {"knowledge_sources": sources}
