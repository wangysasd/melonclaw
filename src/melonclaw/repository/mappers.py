"""数据库记录、游标和稳定 ID 的转换工具。"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID


def _now() -> datetime:
    return datetime.now(UTC)


def _as_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _conversation_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "project_id": str(row["project_id"]) if row["project_id"] is not None else None,
        "project_name": row["project_name"],
        "workdir_path": row["workdir_path"],
        "title": str(row["title"]),
        "is_pinned": bool(row["is_pinned"]),
        "agent_id": str(row["agent_id"]),
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _user_dict(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    user_name = str(row["user_name_zh"])
    tenant_name = str(row["tenant_name_zh"])
    return {
        "user_id": str(row["user_id"]),
        # username 保留现有开发接口字段，值改为用户中文名。
        "username": user_name,
        "user_name_zh": user_name,
        "tenant_id": str(row["tenant_id"]),
        "tenant_role": str(row["tenant_role"]),
        "tenant_status": str(row["tenant_status"]),
        "is_default": str(row["user_id"]) == DEFAULT_SIMULATED_USER_ID,
        "display_name": f"{user_name}-{tenant_name}",
    }


def _project_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "name": str(row["name"]),
        "workdir_path": row["workdir_path"],
        "status": str(row["status"]),
        "is_pinned": bool(row["is_pinned"]),
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _message_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    model_id = row["model_id"]
    model = (
        {
            "id": str(model_id),
            "display_name": str(row["model_display_name"] or ""),
            "provider": str(row["model_provider"] or ""),
            "model": str(row["model_name"] or ""),
        }
        if model_id
        else None
    )
    return {
        "id": str(row["id"]),
        "conversation_id": str(row["conversation_id"]),
        "seq": int(row["seq"]),
        "request_id": str(row["request_id"]),
        "role": str(row["role"]),
        "content": str(row["content"]),
        "status": str(row["status"]),
        "assistant_steps": row["assistant_steps"],
        "execution_duration_ms": row["execution_duration_ms"],
        "display_metadata": row["display_metadata"],
        "error_code": row["error_code"],
        "model": model,
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _conversation_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:30] or "新会话"


def encode_conversation_cursor(updated_at: str, conversation_id: str, is_pinned: bool = False) -> str:
    payload = json.dumps(
        {"updated_at": updated_at, "id": conversation_id, "is_pinned": is_pinned},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_conversation_cursor(cursor: str) -> tuple[bool, datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        updated_at = datetime.fromisoformat(value["updated_at"])
        conversation_id = UUID(value["id"])
        is_pinned = value["is_pinned"]
        if not isinstance(is_pinned, bool):
            raise ValueError("cursor 无效。")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("cursor 无效。") from exc
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return is_pinned, updated_at, conversation_id
