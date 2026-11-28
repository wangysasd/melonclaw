"""知识库管理输入、权限规则与请求级检索额度；不包含正文或凭据。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class KnowledgeError(ValueError):
    status_code = 422


class KnowledgeUnavailable(KnowledgeError):
    status_code = 503

    def __init__(self, message, *, code="unavailable"):
        super().__init__(message)
        self.code = code


class KnowledgeDenied(KnowledgeError):
    status_code = 404


class KnowledgeForbidden(KnowledgeError):
    status_code = 403


class KnowledgeConflict(KnowledgeError):
    status_code = 409


# 库级事件。文档删除和版本发布不进入操作记录。
LIBRARY_EVENT_ACTIONS = (
    "base_created",
    "base_updated",
    "base_suspended",
    "base_restored",
    "base_deleted",
    "ownership_transferred",
    "share_scope_changed",
    "member_granted",
    "member_updated",
    "member_revoked",
)
SHARE_SCOPE_LABELS = {
    "everyone": "所有用户",
    "tenant": "同租户",
    "selected": "指定人",
}
_ACTION_LABELS = {
    "base_created": "创建知识库",
    "base_updated": "更新知识库",
    "base_suspended": "停用",
    "base_restored": "恢复共享",
    "base_deleted": "删除知识库",
    "ownership_transferred": "转让所有权",
    "share_scope_changed": "变更共享范围",
    "member_granted": "授予成员",
    "member_updated": "调整成员",
    "member_revoked": "撤销成员",
}
_FIELD_LABELS = {
    "name": "名称",
    "description": "描述",
    "chunk_config": "分块配置",
    "share_scope": "共享范围",
}
_ROLE_LABELS = {"viewer": "查看者", "editor": "编辑者"}
_UNRECORDED = "未记录"
_DISPOSITION_ACTIONS = frozenset({"base_suspended", "base_restored", "base_deleted"})


def knowledge_operation_item(row: dict, *, kb_name: str) -> dict:
    """把一条知识库事件变成可展示的字段。不补造缺失的原因或范围。"""

    details = row.get("safe_details") if isinstance(row.get("safe_details"), dict) else {}
    action = str(row.get("action") or "")
    actor = row.get("actor_user_id")
    if actor is None or actor == "":
        actor_id = None
        actor_label = "系统"
    else:
        actor_id = str(actor)
        name = row.get("user_name_zh")
        actor_label = (
            f"{name}（{actor_id}）" if isinstance(name, str) and name.strip() else actor_id
        )
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "actor_user_id": actor_id,
        "actor_label": actor_label,
        "kb_id": row.get("kb_id"),
        "kb_name": kb_name,
        "action": action,
        "action_label": MODERATION_ACTION_LABELS.get(action, _ACTION_LABELS.get(action, action)),
        "detail": _operation_detail(action, details, row.get("target_id")),
    }


def _operation_detail(action: str, details: dict, target_id) -> str:
    if action == "share_scope_changed":
        previous = SHARE_SCOPE_LABELS.get(str(details.get("previous") or ""))
        current = SHARE_SCOPE_LABELS.get(str(details.get("share_scope") or ""))
        if previous and current:
            return f"{previous} → {current}"
        return _UNRECORDED
    if action in _DISPOSITION_ACTIONS:
        return _recorded_reason(action, details)
    if action == "ownership_transferred":
        previous = details.get("previous_owner")
        new_owner = details.get("new_owner")
        if isinstance(previous, str) and previous and isinstance(new_owner, str) and new_owner:
            return f"{previous} → {new_owner}"
        return _UNRECORDED
    if action == "base_updated":
        fields = details.get("fields")
        if not isinstance(fields, list):
            return _UNRECORDED
        labels = [
            _FIELD_LABELS[field]
            for field in fields
            if isinstance(field, str) and field in _FIELD_LABELS
        ]
        return "、".join(labels) if labels else _UNRECORDED
    if action in {"member_granted", "member_updated", "member_revoked"}:
        target = "" if target_id is None else str(target_id)
        if not target:
            return _UNRECORDED
        if action == "member_revoked":
            return f"成员 {target}"
        role = _ROLE_LABELS.get(str(details.get("role") or ""))
        return f"{role} {target}" if role else _UNRECORDED
    return ""


def _recorded_reason(action: str, details: dict) -> str:
    reason = details.get("reason")
    labels = MODERATION_REASONS.get(action, {})
    if isinstance(reason, str) and reason in labels:
        return labels[reason]
    return _UNRECORDED


def is_system_admin(user_id, tenant_id) -> bool:
    """当前身份必须是 system 租户中的 admin，不沿用租户角色或原登录身份。"""

    return user_id == "admin" and tenant_id == "system"


SUSPEND_REASONS = {
    "report_review": "接到举报，暂停核查",
    "risk_hold": "发现风险，暂停共享",
}
RESTORE_REASONS = {
    "review_passed": "复核通过，恢复共享",
    "report_unfounded": "举报不成立，恢复共享",
}
DELETE_REASONS = {
    "confirmed_violation": "确认违规，删除知识库",
}
MODERATION_REASONS = {
    "base_suspended": SUSPEND_REASONS,
    "base_restored": RESTORE_REASONS,
    "base_deleted": DELETE_REASONS,
}
MODERATION_ACTION_LABELS = {
    "base_suspended": "停用",
    "base_restored": "恢复共享",
    "base_deleted": "删除知识库",
}


def moderation_reason(action, reason):
    labels = MODERATION_REASONS.get(action)
    if not isinstance(reason, str) or labels is None or reason not in labels:
        raise KnowledgeError("处置原因无效。")
    return reason


# 本轮已选择知识库时，这四个只读工具直接可见，不再先走 find_tools。
KNOWLEDGE_TOOL_NAMES = frozenset(
    {
        "list_knowledge_bases",
        "search_knowledge",
        "open_knowledge_document",
    }
)


KNOWLEDGE_INITIAL_SEARCHES_PER_BASE = 3
KNOWLEDGE_SUPPLEMENTARY_SEARCHES_PER_BASE = 2


def consume_knowledge_search_budget(counts, kb_id):
    """在请求上下文的可序列化字典中原子扣减；检查与递增之间没有 await。"""

    key = str(kb_id)
    count = counts.get(key, 0)
    if count >= KNOWLEDGE_INITIAL_SEARCHES_PER_BASE + KNOWLEDGE_SUPPLEMENTARY_SEARCHES_PER_BASE:
        raise KnowledgeError(
            "本轮该知识库已完成 3 次首次/子问题查询和 2 次补检索；"
            "请依据现有证据说明缺口，不要继续循环查询。"
        )
    counts[key] = count + 1
    supplementary = count >= KNOWLEDGE_INITIAL_SEARCHES_PER_BASE
    return {
        "kind": "supplementary" if supplementary else "initial",
        "initial_remaining": max(0, KNOWLEDGE_INITIAL_SEARCHES_PER_BASE - count - 1),
        "supplementary_remaining": (
            KNOWLEDGE_SUPPLEMENTARY_SEARCHES_PER_BASE
            - (count - KNOWLEDGE_INITIAL_SEARCHES_PER_BASE + 1)
            if supplementary
            else KNOWLEDGE_SUPPLEMENTARY_SEARCHES_PER_BASE
        ),
    }


class ChunkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_size: int = Field(default=800, ge=100, le=2000)
    chunk_overlap: int = Field(default=120, ge=0, le=400)

    @model_validator(mode="after")
    def overlap(self):
        if self.chunk_overlap > self.chunk_size // 5:
            raise ValueError("重叠不能超过分块大小的 20%。")
        return self


def require_role(role, operation):
    allowed = {
        "read": {"viewer", "editor", "owner", "system_admin"},
        "documents": {"editor", "owner", "system_admin"},
        "edit": {"owner", "system_admin"},
        "members": {"owner", "system_admin"},
        "delete": {"owner", "system_admin"},
        "transfer": {"owner"},
    }
    if role not in allowed[operation]:
        raise KnowledgeDenied("知识库不存在或无权执行此操作。")
