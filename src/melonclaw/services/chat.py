"""应用业务门面，组合运行时、资源查询和 Agent 执行服务。"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from melonclaw.core.config import Settings
from melonclaw.database import Database
from melonclaw.memory import MemoryService
from melonclaw.repository import (
    AssistantStateConflictError,
    BusinessRepository,
    ConversationBusyError,
    ConversationNotFoundError,
    PreparedMessagePair,
    ProjectNotFoundError,
    RequestConflictError,
    RequestRecord,
    UserContext,
)
from melonclaw.repository.constants import SYSTEM_TENANT_ID
from melonclaw.services.attachments import AttachmentService
from melonclaw.services.conversations import ConversationService
from melonclaw.services.errors import (
    AgentExecutionError,
    InvalidUserError,
    RequestInProgressError,
)
from melonclaw.services.execution import ExecutionService, PreparedExecution
from melonclaw.services.resource_service import (
    ADMIN_ROLES,
    McpServerPayload,
    ModelConfigPayload,
    ProviderConfigPayload,
    ResourcePermissionError,
    ResourceService,
)
from melonclaw.services.runtime import ChatRuntime
from melonclaw.services.skill_import import SkillImportService
from melonclaw.services.skill_remote import (
    fetch_remote_skill_archive,
    filter_archive_subdir,
    pin_remote_source,
    resolve_remote_skill_source,
)
from melonclaw.services.user_input_execution import UserInputExecutionService

logger = logging.getLogger(__name__)

USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ChatService:
    """持久化聊天服务；保留统一门面以隔离 HTTP 层和内部服务组件。"""

    def __init__(self, runtime: ChatRuntime | None = None) -> None:
        self.runtime = runtime or ChatRuntime()
        self.conversations = ConversationService(self.runtime)
        self.attachments = AttachmentService(self.runtime, self.conversations)
        self.runtime.attachment_hydration_provider = self.attachments.hydration_provider
        self.execution = ExecutionService(
            self.runtime,
            self.conversations,
            attachments=self.attachments,
        )
        self.user_input = UserInputExecutionService(self.execution)
        self._resources: ResourceService | None = None
        self._skill_imports: SkillImportService | None = None

    @property
    def resources(self) -> ResourceService:
        if self._resources is None:
            self._resources = ResourceService(self.runtime)
        return self._resources

    @property
    def skill_imports(self) -> SkillImportService:
        """ZIP 导入草稿服务；初始化需要 data_root，首次使用时惰性创建。"""

        if self._skill_imports is None:
            settings = self.runtime.settings
            if settings is None:
                raise RuntimeError("运行配置尚未加载。")
            self._skill_imports = SkillImportService(settings.data_root)
        return self._skill_imports

    @property
    def settings(self) -> Settings | None:
        return self.runtime.settings

    @property
    def database(self) -> Database | None:
        return self.runtime.database

    @property
    def storage(self) -> BusinessRepository | None:
        return self.runtime.storage

    @property
    def checkpoint_pool(self) -> AsyncConnectionPool | None:
        return self.runtime.checkpoint_pool

    @property
    def checkpointer(self) -> AsyncPostgresSaver | None:
        return self.runtime.checkpointer

    @property
    def memory_store_context(self) -> Any | None:
        return self.runtime.memory_store_context

    @property
    def memory_store(self) -> Any | None:
        return self.runtime.memory_store

    @property
    def memory_service(self) -> MemoryService | None:
        return self.runtime.memory_service

    @property
    def workspace_agents(
        self,
    ) -> dict[tuple[str, tuple[str, int, str, str, str], tuple[str, ...]], Any] | None:
        return self.runtime.workspace_agents

    @property
    def startup_error(self) -> str | None:
        return self.runtime.startup_error

    @property
    def worker_id(self) -> str:
        return self.runtime.worker_id

    async def initialize(self) -> None:
        await self.runtime.initialize()
        await self.attachments.start()

    async def close(self) -> None:
        await self.attachments.close()
        await self.runtime.close()

    @property
    def ready(self) -> bool:
        return self.runtime.ready

    async def status(self) -> dict[str, Any]:
        return await self.runtime.status()

    async def resolve_user(self, user_id: str) -> UserContext:
        return await self.conversations.resolve_user(user_id)

    async def users(self) -> dict[str, Any]:
        return await self.conversations.users()

    async def create_user(
        self,
        actor_user_id: str,
        user_id: str,
        user_name_zh: str,
    ) -> None:
        """admin 创建普通用户：落在系统租户，角色 member。

        权限矩阵与全局资源一致：仅 admin/owner 可创建用户；
        用户 ID 全局唯一且只允许小写字母、数字、下划线和连字符。
        """

        actor = await self.conversations.resolve_user(actor_user_id)
        if actor.tenant_role not in ADMIN_ROLES:
            raise ResourcePermissionError("该操作需要管理员权限。")
        if not USER_ID_RE.fullmatch(user_id):
            raise ValueError("用户 ID 只能包含小写字母、数字、下划线和连字符。")
        if not user_name_zh or len(user_name_zh) > 3:
            raise ValueError("显示名称长度须在 1 到 3 个字符之间。")
        storage = self._require_ready()
        if await storage.user_exists(user_id):
            raise ValueError(f"用户 {user_id!r} 已存在。")
        await storage.create_user(
            user_id=user_id,
            user_name_zh=user_name_zh,
            tenant_id=SYSTEM_TENANT_ID,
            tenant_role="member",
        )

    async def models(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """校验用户的唯一租户归属后返回其可见的模型目录。"""

        await self.conversations.resolve_user(user_id)
        return await self.runtime.models(user_id)

    async def skills(self, user_id: str) -> dict[str, Any]:
        """返回该用户可见的 Skill 目录（共享 + 私有）。"""

        await self.conversations.resolve_user(user_id)
        return await self.runtime.skills(user_id)

    async def manageable_skills(self, user_id: str) -> dict[str, Any]:
        """返回该用户可管理的 Skill 全集（资源管理界面用）。"""

        await self.conversations.resolve_user(user_id)
        return await self.resources.manageable_skills(user_id)

    async def download_skill_archive(
        self, user_id: str, name: str, scope: str
    ) -> bytes:
        await self.conversations.resolve_user(user_id)
        return await self.resources.download_skill_archive(user_id, name, scope)

    async def set_skill_enabled(
        self, user_id: str, name: str, enabled: bool, scope: str
    ) -> None:
        await self.resources.set_skill_enabled(user_id, name, enabled, scope)

    async def set_skill_global_enabled(
        self, user_id: str, name: str, enabled: bool
    ) -> None:
        """全员启停共享 Skill；仅 admin/owner。"""

        await self.resources.set_skill_global_enabled(user_id, name, enabled)

    async def delete_skill(
        self, user_id: str, name: str, scope: str
    ) -> None:
        await self.resources.delete_skill(user_id, name, scope)

    async def prepare_skill_import(
        self, user_id: str, archive_bytes: bytes, target_id: str | None = None
    ) -> dict[str, Any]:
        await self.conversations.resolve_user(user_id)
        draft = await self.skill_imports.prepare(
            user_id=user_id,
            archive_bytes=archive_bytes,
            target_id=target_id,
            storage=self.runtime.require_ready(),
        )
        return draft.public_dict()

    async def prepare_remote_skill_install(
        self, user_id: str, repo: str, target_id: str | None = None
    ) -> dict[str, Any]:
        """从 GitHub 远程市场下载 Skill 包并进入两段式确认。"""

        await self.conversations.resolve_user(user_id)
        source = await pin_remote_source(resolve_remote_skill_source(repo))
        archive = await fetch_remote_skill_archive(source.url)
        if source.subpath is not None:
            archive = filter_archive_subdir(archive, source.subpath)
        draft = await self.skill_imports.prepare(
            user_id=user_id,
            archive_bytes=archive,
            storage=self.runtime.require_ready(),
            source_type="remote",
            target_id=target_id,
            source_url=source.source_url,
            source_ref=source.ref,
        )
        return draft.public_dict()

    async def skill_details(self, user_id: str, name: str, scope: str) -> dict[str, Any]:
        return await self.resources.skill_details(user_id, name, scope)

    async def recover_skills(self, user_id: str) -> dict[str, Any]:
        return await self.resources.recover_skills(user_id)

    async def confirm_skill_import(self, user_id: str, draft_id: str) -> None:
        await self.skill_imports.confirm(
            draft_id=draft_id,
            user_id=user_id,
            storage=self.runtime.require_ready(),
        )

    async def cancel_skill_import(self, user_id: str, draft_id: str) -> None:
        await self.skill_imports.cancel(draft_id=draft_id, user_id=user_id)

    async def list_mcp(self, user_id: str) -> dict[str, Any]:
        return await self.resources.list_mcp(user_id)

    async def create_mcp(self, user_id: str, payload: McpServerPayload) -> None:
        await self.resources.create_mcp(user_id, payload)

    async def update_mcp(
        self,
        user_id: str,
        slug: str,
        *,
        enabled: bool | None = None,
        tool_allowlist: Any = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if enabled is not None:
            kwargs["enabled"] = enabled
        if tool_allowlist is not None:
            kwargs["tool_allowlist"] = tool_allowlist
        await self.resources.update_mcp(user_id, slug, **kwargs)

    async def delete_mcp(self, user_id: str, slug: str) -> None:
        await self.resources.delete_mcp(user_id, slug)

    async def manageable_models(self, user_id: str) -> dict[str, Any]:
        """返回该用户可管理的自定义模型全集（资源管理界面用）。"""

        await self.conversations.resolve_user(user_id)
        return await self.resources.list_models(user_id)

    async def create_model(self, user_id: str, payload: ModelConfigPayload) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.create_model(user_id, payload)

    async def update_model(
        self,
        user_id: str,
        model_key: str,
        *,
        enabled: bool | None = None,
        display_name: str | None = None,
        model_name: str | None = None,
        is_default: bool | None = None,
    ) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.update_model(
            user_id,
            model_key,
            enabled=enabled,
            display_name=display_name,
            model_name=model_name,
            is_default=is_default,
        )

    async def manageable_providers(self, user_id: str) -> dict[str, Any]:
        """返回该用户可管理的模型供应商全集（资源管理界面用）。"""

        await self.conversations.resolve_user(user_id)
        return await self.resources.list_providers(user_id)

    async def create_provider(
        self, user_id: str, payload: ProviderConfigPayload
    ) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.create_provider(user_id, payload)

    async def update_provider(
        self,
        user_id: str,
        provider_key: str,
        *,
        enabled: bool | None = None,
        display_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        models_endpoint: str | None = None,
        api_key_env: str | None = None,
        request_headers: dict[str, str] | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.update_provider(
            user_id,
            provider_key,
            enabled=enabled,
            display_name=display_name,
            base_url=base_url,
            api_key=api_key,
            models_endpoint=models_endpoint,
            api_key_env=api_key_env,
            request_headers=request_headers,
            extra_config=extra_config,
        )

    async def delete_provider(self, user_id: str, provider_key: str) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.delete_provider(user_id, provider_key)

    async def set_user_provider_key(
        self, user_id: str, provider_key: str, api_key: str
    ) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.set_user_provider_key(user_id, provider_key, api_key)

    async def delete_user_provider_key(
        self, user_id: str, provider_key: str
    ) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.delete_user_provider_key(user_id, provider_key)

    async def fetch_remote_models(
        self, user_id: str, provider_key: str
    ) -> dict[str, Any]:
        await self.conversations.resolve_user(user_id)
        return await self.resources.fetch_remote_models(user_id, provider_key)

    async def delete_model(self, user_id: str, model_key: str) -> None:
        await self.conversations.resolve_user(user_id)
        await self.resources.delete_model(user_id, model_key)

    def attachment_capabilities(self) -> dict[str, Any]:
        """返回附件支持类型与限制清单，供前端做上传前预校验。"""

        return self.attachments.capabilities()

    def _require_ready(self) -> BusinessRepository:
        return self.runtime.require_ready()

    def _project_workspace_dir(self, project: dict[str, Any]):
        return self.runtime.project_workspace_dir(project)

    @staticmethod
    def _conversation_config(conversation_id: UUID) -> dict[str, Any]:
        return ChatRuntime.conversation_config(conversation_id)

    async def create_project(
        self,
        user_id: str,
        name: str,
    ) -> dict[str, Any]:
        return await self.conversations.create_project(user_id, name)

    async def list_projects(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        return await self.conversations.list_projects(user_id)

    async def update_project(
        self, project_id: UUID, user_id: str,
        *, name: str | None = None, is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        return await self.conversations.update_project(
            project_id, user_id, name=name, is_pinned=is_pinned, delete=delete,
        )

    async def update_conversation(
        self, conversation_id: UUID, user_id: str,
        *, title: str | None = None, is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        return await self.conversations.update_conversation(
            conversation_id, user_id, title=title, is_pinned=is_pinned, delete=delete,
        )

    async def move_conversation_to_project(
        self, conversation_id: UUID, project_id: UUID, user_id: str,
    ) -> dict[str, Any]:
        return await self.conversations.move_conversation_to_project(
            conversation_id, project_id, user_id,
        )

    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        return await self.conversations.create_conversation(
            user_id,
            project_id,
        )

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: str | None,
        project_id: UUID | None = None,
        scope: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        return await self.conversations.list_conversations(
            user_id,
            limit=limit,
            cursor=cursor,
            project_id=project_id,
            scope=scope,
        )

    async def history(
        self,
        conversation_id: UUID,
        user_id: str,
        *,
        limit: int,
        before_seq: int | None,
    ) -> dict[str, Any]:
        return await self.conversations.history(
            conversation_id,
            user_id,
            limit=limit,
            before_seq=before_seq,
        )

    async def prepare_message(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
        content: str,
        model_id: str | None = None,
        skill_id: str | None = None,
        attachment_ids: list[UUID] | None = None,
        capabilities: list[str] | None = None,
    ) -> PreparedExecution:
        await self._release_stale_user_interaction(conversation_id, user_id)
        return await self.execution.prepare_message(
            conversation_id,
            user_id,
            request_id,
            content,
            model_id=model_id,
            skill_id=skill_id,
            attachment_ids=attachment_ids,
            capabilities=capabilities,
        )

    async def _release_stale_user_interaction(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> None:
        """发新消息前一次判断并收尾过期问题或待人工结束的失败轮次。"""

        try:
            await self.user_input.cancel_before_new_message(
                conversation_id,
                user_id,
            )
        except Exception:  # noqa: BLE001 - 自动解锁失败不应阻塞新消息
            logger.warning(
                "自动收尾用户问题失败：conversation_id=%s",
                conversation_id,
                exc_info=True,
            )

    async def prepare_approval(
        self,
        conversation_id: UUID,
        user_id: str,
        decisions: Any,
        *,
        approval_batch_id: UUID | None = None,
        assistant_message_id: UUID | None = None,
    ) -> tuple[PreparedExecution, Any]:
        return await self.execution.prepare_approval(
            conversation_id,
            user_id,
            decisions,
            approval_batch_id=approval_batch_id,
            assistant_message_id=assistant_message_id,
        )

    async def prepare_user_input(
        self,
        conversation_id: UUID,
        user_id: str,
        interaction_id: UUID,
        assistant_message_id: UUID,
        decision_request_id: str,
        answer: Any,
    ):
        return await self.user_input.prepare(
            conversation_id,
            user_id,
            interaction_id,
            assistant_message_id,
            decision_request_id,
            answer,
        )

    async def stream_execution(
        self,
        execution: PreparedExecution,
        *,
        agent_input: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self.execution.stream_execution(
            execution,
            agent_input=agent_input,
        ):
            yield event


__all__ = [
    "AgentExecutionError",
    "AssistantStateConflictError",
    "ChatService",
    "ConversationBusyError",
    "ConversationNotFoundError",
    "InvalidUserError",
    "PreparedExecution",
    "PreparedMessagePair",
    "ProjectNotFoundError",
    "RequestConflictError",
    "RequestInProgressError",
    "RequestRecord",
    "UserContext",
]
