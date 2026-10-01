"""API 请求模型。"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# 传输层护栏，防止超大 JSON 体进入业务层；附件实际业务上限来自
# ``Settings.attachment_max_per_message``，由 ExecutionService / Repository 校验。
MAX_ATTACHMENT_IDS_PER_MESSAGE = 200


class AssistantToolCallSchema(BaseModel):
    """assistant step 内可展示的工具调用快照。"""

    call_id: str
    name: str
    batch_index: int
    args_preview: str | None = None
    result_preview: str | None = None
    status: str
    error: str | None = None
    # epoch 毫秒；只在真实观测到调用/结果时写入，缺省表示没有可靠耗时。
    started_at: int | None = None
    completed_at: int | None = None


class AssistantStepSchema(BaseModel):
    """一次根 Agent AIMessage 的可见展示快照。"""

    id: str
    ordinal: int
    source_message_id: str | None = None
    content: str
    status: str
    is_final: bool
    tool_calls: list[AssistantToolCallSchema]
    truncated: bool = False


class ConversationRequest(BaseModel):
    """创建会话时的开发用户和可选 Project。"""

    user_id: str = Field(min_length=1, max_length=64)
    project_id: UUID | None = None


class ConversationMoveRequest(BaseModel):
    """将当前用户的普通会话加入其已有项目。"""

    user_id: str = Field(min_length=1, max_length=64)
    project_id: UUID


class ProjectRequest(BaseModel):
    """创建 Project 时的开发用户和名称。"""

    user_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class ResourceUpdateRequest(BaseModel):
    """项目或会话的名称与置顶状态；路由按资源选择对应字段。"""

    user_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None


class MessageRequest(BaseModel):
    """浏览器发送给 Agent 的一轮消息和模型选择。"""

    user_id: str = Field(min_length=1, max_length=64)
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
    """浏览器提交的 HITL 审批决定。"""

    user_id: str = Field(min_length=1, max_length=64)
    # 审批恢复必须绑定当前展示给用户的批次和助手消息，避免刷新后提交旧卡片。
    approval_batch_id: UUID
    assistant_message_id: UUID
    decisions: list[dict[str, Any]]


class UserInputRequest(BaseModel):
    """浏览器提交的结构化用户问题答案。"""

    user_id: str = Field(min_length=1, max_length=64)
    interaction_id: UUID
    assistant_message_id: UUID
    decision_request_id: UUID
    answer: dict[str, Any]


class SkillImportConfirmRequest(BaseModel):
    """确认或取消一个待安装的 Skill 导入草稿。"""

    user_id: str = Field(min_length=1, max_length=64)
    draft_id: str = Field(min_length=1, max_length=64)


class SkillUpdateRequest(BaseModel):
    """个人启用/停用：共享 Skill 只影响自己，私有 Skill 仅限创建者。

    scope 必填，明确选择共享或自己的私有 Skill。
    """

    user_id: str = Field(min_length=1, max_length=64)
    enabled: bool
    scope: Literal["global", "user"]


class SkillGlobalStateRequest(BaseModel):
    """全员启用/停用共享 Skill（写 skills.enabled）；仅 admin/owner。"""

    user_id: str = Field(min_length=1, max_length=64)
    enabled: bool


class ModelProviderCreateRequest(BaseModel):
    """新建模型供应商；api_key 只写不回读，允许为空待补。"""

    user_id: str = Field(min_length=1, max_length=64)
    provider_key: str = Field(min_length=1, max_length=64)
    scope: Literal["global"]
    display_name: str = Field(min_length=1, max_length=120)
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    api_key_env: str = Field(default="", max_length=120)
    request_headers: dict[str, str] = Field(default_factory=dict, max_length=64)
    extra_config: dict[str, Any] = Field(default_factory=dict, max_length=64)
    base_url: str = Field(min_length=1, max_length=400)
    api_key: str | None = Field(default=None, max_length=240)
    models_endpoint: str | None = Field(default=None, max_length=300)
    enabled: bool = True


class ModelProviderUpdateRequest(BaseModel):
    """更新模型供应商；所有字段缺省表示不改动，api_key 缺省保留已存密钥。"""

    user_id: str = Field(min_length=1, max_length=64)
    api_key_env: str | None = Field(default=None, max_length=120)
    request_headers: dict[str, str] | None = Field(default=None, max_length=64)
    extra_config: dict[str, Any] | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=400)
    api_key: str | None = Field(default=None, max_length=240)
    models_endpoint: str | None = Field(default=None, max_length=300)


class ModelProviderTestRequest(BaseModel):
    """临时测试供应商的只读模型列表连接，不保存配置或凭据。"""

    user_id: str = Field(min_length=1, max_length=64)
    provider_key: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=400)
    models_endpoint: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=240)
    api_key_env: str = Field(default="", max_length=120)
    request_headers: dict[str, str] | None = Field(default=None, max_length=64)


class UserProviderKeyRequest(BaseModel):
    """普通用户在共享供应商上设置自己的 Key；只写不回读。"""

    user_id: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1, max_length=240)


class ModelConfigCreateRequest(BaseModel):
    """新建自定义模型；连接与凭据归属供应商，模型只记名称。"""

    user_id: str = Field(min_length=1, max_length=64)
    model_key: str = Field(min_length=1, max_length=64)
    provider_key: str = Field(min_length=1, max_length=64)
    scope: Literal["global", "user"]
    display_name: str = Field(min_length=1, max_length=120)
    model_name: str = Field(min_length=1, max_length=160)
    enabled: bool = True


class ModelConfigUpdateRequest(BaseModel):
    """更新自定义模型；所有字段缺省表示不改动。

    ``is_default=True`` 设置默认模型：管理员管理全局默认，用户管理个人默认。
    连接与凭据的变更走供应商管理接口。
    """

    user_id: str = Field(min_length=1, max_length=64)
    enabled: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    model_name: str | None = Field(default=None, min_length=1, max_length=160)
    is_default: bool | None = None


class DevUserCreateRequest(BaseModel):
    """admin 创建普通用户（开发模拟身份，非生产认证）。

    新用户落在系统租户（system），角色 member；user_id 全局唯一。
    """

    actor_user_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    user_name_zh: str = Field(min_length=1, max_length=3)


class SkillRemoteInstallRequest(BaseModel):
    """从远程市场安装 Skill（仅支持 GitHub zipball）。"""

    user_id: str = Field(min_length=1, max_length=64)
    repo: str = Field(min_length=1, max_length=200)
    target_id: UUID | None = None
