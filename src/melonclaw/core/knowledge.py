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


class KnowledgeConflict(KnowledgeError):
    status_code = 409


# 本轮已选择知识库时，这四个只读工具直接可见，不再先走 find_tools。
KNOWLEDGE_TOOL_NAMES = frozenset({
    "list_knowledge_bases",
    "search_knowledge",
    "open_knowledge_document",
})


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
            KNOWLEDGE_SUPPLEMENTARY_SEARCHES_PER_BASE - (count - KNOWLEDGE_INITIAL_SEARCHES_PER_BASE + 1)
            if supplementary else KNOWLEDGE_SUPPLEMENTARY_SEARCHES_PER_BASE
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
