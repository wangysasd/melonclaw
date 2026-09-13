"""数据库记录、游标和稳定 ID 的转换工具。"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from melonclaw.database.constants import DEFAULT_PROJECT_NAME
from melonclaw.repository.constants import DEFAULT_SIMULATED_USER_ID


def _now() -> datetime:
    return datetime.now(UTC)


def _as_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _conversation_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    project_name = row.get("project_name") if hasattr(row, "get") else None
    workdir_path = row.get("workdir_path") if hasattr(row, "get") else None
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "project_id": str(row["project_id"]),
        "project_name": str(project_name or DEFAULT_PROJECT_NAME),
        "workdir_path": str(workdir_path or ""),
        "title": str(row["title"]),
        "agent_id": str(row["agent_id"]),
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _user_dict(
    row: Mapping[str, Any],
    *,
    tenant_ids: list[str] | None = None,
    tenant_names: list[str] | None = None,
    tenant_memberships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    user_name = str(row["user_name_zh"])
    normalized_tenant_ids = tenant_ids or [str(row["tenant_id"])]
    normalized_tenant_names = tenant_names or [str(row["tenant_name_zh"])]
    tenant_name = "、".join(normalized_tenant_names)
    default_tenant_id = normalized_tenant_ids[0]
    return {
        "user_id": str(row["user_id"]),
        # username 保留现有开发接口字段，值改为用户中文名。
        "username": user_name,
        "user_name_zh": user_name,
        # 保留 tenant_id 作为当前开发上下文的默认租户；完整关系见 tenant_ids。
        "tenant_id": default_tenant_id,
        "default_tenant_id": default_tenant_id,
        "tenant_ids": normalized_tenant_ids,
        "tenant_names": normalized_tenant_names,
        "tenant_name": tenant_name,
        "tenant_name_zh": tenant_name,
        "tenant_role": str(row.get("role") or "member"),
        "tenant_status": str(row.get("status") or "active"),
        "tenant_memberships": tenant_memberships or [
            {
                "tenant_id": normalized_tenant_ids[0],
                "tenant_name": normalized_tenant_names[0],
                "role": str(row.get("role") or "member"),
                "status": str(row.get("status") or "active"),
            }
        ],
        "is_default": str(row["user_id"]) == DEFAULT_SIMULATED_USER_ID,
        "display_name": f"{user_name}-{tenant_name}",
    }


def _project_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "name": str(row["name"]),
        "workdir_path": str(row["workdir_path"]),
        "status": str(row["status"]),
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
        "is_default": bool(row["is_default"]),
    }


def _message_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    model_id = row.get("model_id") if hasattr(row, "get") else None
    model = (
        {
            "id": str(model_id),
            "display_name": str(row.get("model_display_name") or ""),
            "provider": str(row.get("model_provider") or ""),
            "model": str(row.get("model_name") or ""),
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
        "content": str(row["content"] or ""),
        "status": str(row["status"]),
        "display_metadata": row["display_metadata"] or {},
        "error_code": row["error_code"],
        "model": model,
        "created_at": _as_iso(row["created_at"]),
        "updated_at": _as_iso(row["updated_at"]),
    }


def _conversation_title(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:30] or "新会话"


def _default_project_id(user_id: str) -> UUID:
    """为用户生成可重复计算的默认 Project ID。"""

    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:default-project:{user_id}",
    )


def _user_tenant_id(user_id: str, tenant_id: str) -> UUID:
    """为演示环境的用户租户关系生成稳定的代理主键。"""

    return uuid5(
        NAMESPACE_URL,
        f"melonclaw:user-tenant:{user_id}:{tenant_id}",
    )


def encode_conversation_cursor(updated_at: str, conversation_id: str) -> str:
    payload = json.dumps(
        {"updated_at": updated_at, "id": conversation_id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_conversation_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        updated_at = datetime.fromisoformat(value["updated_at"])
        conversation_id = UUID(value["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("cursor 无效。") from exc
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at, conversation_id

