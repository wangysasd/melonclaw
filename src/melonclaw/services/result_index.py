"""从最终回答的 Markdown 语法提取交付引用，不扫描工作区或执行过程。"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote
from uuid import UUID

from markdown_it import MarkdownIt

from melonclaw.storage.results import result_path_parts

_MARKDOWN = MarkdownIt("commonmark", {"html": False})
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", re.IGNORECASE)


def asset_ref(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or len(value) != 1:
        return None
    path = value.get("path")
    if isinstance(path, str):
        try:
            result_path_parts(path)
        except ValueError:
            return None
        return {"path": path}
    attachment = value.get("attachment_id")
    if isinstance(attachment, str) and _UUID.fullmatch(attachment):
        return {"attachment_id": str(UUID(attachment))}
    return None


def asset_key(ref: dict[str, str]) -> str:
    return ref["path"] if "path" in ref else f"/attachments/{ref['attachment_id']}"


def _href_ref(href: str) -> dict[str, str] | None:
    href = unquote(href)
    if href.startswith("/attachments/"):
        return asset_ref({"attachment_id": href[len("/attachments/"):]})
    return asset_ref({"path": href})


def extract_result_refs(content: str) -> list[dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}
    lines = content.splitlines()
    for token in _MARKDOWN.parse(content):
        if token.type == "fence" and token.info.split() and token.info.split()[0].lower() == "melon-result":
            if token.map is None or token.map[1] <= token.map[0] + 1 or not re.fullmatch(
                rf"(?:[ \t]*>[ \t]?)*[ \t]*{re.escape(token.markup[0])}{{{len(token.markup)},}}[ \t]*",
                lines[token.map[1] - 1],
            ):
                continue
            if len(token.content) > 200_000:
                continue
            try:
                value = json.loads(token.content)
            except (ValueError, RecursionError):
                continue
            if (
                not isinstance(value, dict)
                or type(value.get("version")) is not int or value["version"] != 1
                or value.get("type") not in ("file", "image")
                or set(value) - {"version", "type", "ref", "caption"}
                or ("caption" in value and (
                    not isinstance(value["caption"], str)
                    or not value["caption"].strip() or len(value["caption"]) > 6000
                ))
            ):
                continue
            ref = asset_ref(value.get("ref"))
            if ref:
                refs[asset_key(ref)] = ref
        elif token.type == "inline":
            for child in token.children or []:
                href = child.attrGet("href") if child.type == "link_open" else (
                    child.attrGet("src") if child.type == "image" else None
                )
                if href:
                    ref = _href_ref(href)
                    if ref:
                        refs[asset_key(ref)] = ref
    return list(refs.values())


def message_result_refs(message: dict[str, Any]) -> list[dict[str, str]]:
    if message["role"] != "assistant" or message["status"] != "completed":
        return []
    return extract_result_refs(message["content"])
