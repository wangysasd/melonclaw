"""API 请求模型。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationRequest(BaseModel):
    """创建会话时的开发用户和当前租户运行上下文；会话本身不绑定租户。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: UUID | None = None


class ProjectRequest(BaseModel):
    """创建 Project 时的开发用户、租户和名称。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class MessageRequest(BaseModel):
    """浏览器发送给 Agent 的一轮消息、模型选择和用户标签。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    request_id: UUID
    content: str = Field(min_length=1, max_length=12000)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)


class ApprovalRequest(BaseModel):
    """浏览器提交的 HITL 审批决定和可选用户标签。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    decisions: list[dict[str, Any]]
