"""API 请求模型。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# 传输层护栏，防止超大 JSON 体进入业务层；真正的业务上限来自
# ``Settings.attachment_max_per_message``，由 ExecutionService / Repository 校验。
MAX_ATTACHMENT_IDS_PER_MESSAGE = 200


class ConversationRequest(BaseModel):
    """创建会话时的开发用户和当前租户运行上下文；会话本身不绑定租户。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    project_id: UUID


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
    content: str = Field(default="", max_length=12000)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    skill_id: str | None = Field(default=None, min_length=1, max_length=120)
    attachment_ids: list[UUID] = Field(
        default_factory=list, max_length=MAX_ATTACHMENT_IDS_PER_MESSAGE
    )
    # 客户端能力协商：未声明 user_input_v1 的客户端不会拿到 ask_user 工具，
    # 避免旧前端收到一张渲染不出来的问题卡片。未知能力一律忽略。
    capabilities: list[str] = Field(default_factory=list, max_length=32)


class ApprovalRequest(BaseModel):
    """浏览器提交的 HITL 审批决定和可选用户标签。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    # 审批恢复必须绑定当前展示给用户的批次和助手消息，避免刷新后提交旧卡片。
    approval_batch_id: UUID | None = None
    assistant_message_id: UUID | None = None
    decisions: list[dict[str, Any]]


class UserInputRequest(BaseModel):
    """浏览器提交的结构化用户问题答案。"""

    user_id: str = Field(min_length=1, max_length=64)
    tenant_id: str | None = Field(default=None, min_length=1, max_length=64)
    interaction_id: UUID
    assistant_message_id: UUID
    decision_request_id: UUID
    answer: dict[str, Any]
