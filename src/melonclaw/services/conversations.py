"""Project、Conversation 和历史消息业务。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from melonclaw.core.hitl import aget_pending_approval, serialize_pending_approval
from melonclaw.repository import (
    BusinessRepository,
    ConversationNotFoundError,
    ProjectNotFoundError,
    UserContext,
)
from melonclaw.services.errors import InvalidUserError
from melonclaw.services.runtime import ChatRuntime


class ConversationService:
    """处理用户上下文校验后的 Project/Conversation 业务。"""

    def __init__(self, runtime: ChatRuntime) -> None:
        self.runtime = runtime

    async def resolve_user(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> UserContext:
        """解析用户及可选租户标签；Project/Conversation 只归属用户。"""

        storage = self.runtime.storage
        if storage is None:
            raise RuntimeError(self.runtime.startup_error or "数据库仍在启动，请稍候。")
        clean_user_id = user_id.strip()
        clean_tenant_id = tenant_id.strip() if tenant_id is not None else None
        if not clean_user_id:
            raise InvalidUserError("user_id 不能为空。")
        context = await storage.get_user_context(clean_user_id, clean_tenant_id)
        if context is None:
            raise InvalidUserError("用户不存在或没有租户标签。")
        await storage.ensure_default_project(context.user_id)
        return context

    async def users(self) -> dict[str, Any]:
        storage = self.runtime.storage
        if storage is None:
            raise RuntimeError(self.runtime.startup_error or "数据库仍在启动，请稍候。")
        return {"items": await storage.list_users()}

    async def project_for_conversation(
        self,
        storage: BusinessRepository,
        conversation: dict[str, Any],
        context: UserContext,
    ) -> dict[str, Any]:
        project = await storage.get_project(
            UUID(conversation["project_id"]),
            context.user_id,
        )
        if project is None:
            raise ProjectNotFoundError
        return project

    async def create_project(
        self,
        user_id: str,
        name: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        clean_name = " ".join(name.split()).strip()
        if not clean_name:
            raise ValueError("Project 名称不能为空。")
        if len(clean_name) > 120:
            raise ValueError("Project 名称不能超过 120 个字符。")
        project = await storage.create_project(context.user_id, clean_name)
        self.runtime.project_workspace_dir(project)
        return project

    async def list_projects(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        return await storage.list_projects(context.user_id)

    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        if project_id is None:
            # 兼容旧 API：未选择 Project 时，自动使用用户唯一的临时会话。
            project = await storage.ensure_default_project(context.user_id)
        else:
            project = await storage.get_project(project_id, context.user_id)
            if project is None:
                raise ProjectNotFoundError
        self.runtime.project_workspace_dir(project)
        return await storage.create_conversation(
            context.user_id,
            UUID(project["id"]),
        )

    async def list_conversations(
        self,
        user_id: str,
        *,
        tenant_id: str | None = None,
        limit: int,
        cursor: str | None,
        project_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        if project_id is not None and await storage.get_project(
            project_id,
            context.user_id,
        ) is None:
            raise ProjectNotFoundError
        return await storage.list_conversations(
            context.user_id,
            limit=limit,
            cursor=cursor,
            project_id=project_id,
        )

    async def history(
        self,
        conversation_id: UUID,
        user_id: str,
        *,
        tenant_id: str | None = None,
        limit: int,
        before_seq: int | None,
    ) -> dict[str, Any]:
        storage = self.runtime.require_ready()
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        # Conversation 只按 user_id + project_id 归属；tenant_id 仅用于
        # 校验当前运行上下文并加载对应的 Tenant Memory。
        context = await self.resolve_user(user_id, tenant_id)
        conversation, messages, next_before_seq = await storage.list_messages(
            conversation_id,
            context.user_id,
            limit=limit,
            before_seq=before_seq,
        )
        project = await self.project_for_conversation(
            storage,
            conversation,
            context,
        )
        incomplete = await storage.get_incomplete_assistant(
            conversation_id,
            context.user_id,
        )
        model = (
            self.runtime.model_for_message(incomplete)
            if incomplete is not None
            else self.runtime.resolve_model()
        )
        agent = await self.runtime.agent_for_project(project, model)
        pending = await aget_pending_approval(
            agent,
            self.runtime.conversation_config(conversation_id),
        )
        return {
            "conversation": conversation,
            "items": messages,
            "next_before_seq": next_before_seq,
            "pending_approval": serialize_pending_approval(pending) if pending else None,
        }
