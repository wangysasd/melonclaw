"""应用业务门面，组合运行时、资源查询和 Agent 执行服务。"""

from __future__ import annotations

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
from melonclaw.services.conversations import ConversationService
from melonclaw.services.errors import (
    AgentExecutionError,
    InvalidUserError,
    RequestInProgressError,
)
from melonclaw.services.execution import ExecutionService, PreparedExecution
from melonclaw.services.runtime import ChatRuntime


class ChatService:
    """持久化聊天服务；保留统一门面以隔离 HTTP 层和内部服务组件。"""

    def __init__(self, runtime: ChatRuntime | None = None) -> None:
        self.runtime = runtime or ChatRuntime()
        self.conversations = ConversationService(self.runtime)
        self.execution = ExecutionService(self.runtime, self.conversations)

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
    def project_agents(
        self,
    ) -> dict[tuple[str, tuple[str, int, str, str, str]], Any] | None:
        return self.runtime.project_agents

    @property
    def startup_error(self) -> str | None:
        return self.runtime.startup_error

    @property
    def worker_id(self) -> str:
        return self.runtime.worker_id

    async def initialize(self) -> None:
        await self.runtime.initialize()

    async def close(self) -> None:
        await self.runtime.close()

    @property
    def ready(self) -> bool:
        return self.runtime.ready

    def status(self) -> dict[str, Any]:
        return self.runtime.status()

    async def resolve_user(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> UserContext:
        return await self.conversations.resolve_user(user_id, tenant_id)

    async def users(self) -> dict[str, Any]:
        return await self.conversations.users()

    async def models(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        """校验当前用户租户上下文后返回系统模型目录。"""

        await self.conversations.resolve_user(user_id, tenant_id)
        return self.runtime.models()

    def _require_ready(self) -> BusinessRepository:
        return self.runtime.require_ready()

    def _project_workspace_dir(self, project: dict[str, Any]):
        return self.runtime.project_workspace_dir(project)

    async def _agent_for_project(self, project: dict[str, Any]) -> Any:
        return await self.runtime.agent_for_project(project)

    @staticmethod
    def _conversation_config(conversation_id: UUID) -> dict[str, Any]:
        return ChatRuntime.conversation_config(conversation_id)

    async def create_project(
        self,
        user_id: str,
        name: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.conversations.create_project(user_id, name, tenant_id)

    async def list_projects(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.conversations.list_projects(user_id, tenant_id)

    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.conversations.create_conversation(
            user_id,
            project_id,
            tenant_id,
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
        return await self.conversations.list_conversations(
            user_id,
            tenant_id=tenant_id,
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
        return await self.conversations.history(
            conversation_id,
            user_id,
            tenant_id=tenant_id,
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
        tenant_id: str | None = None,
    ) -> PreparedExecution:
        return await self.execution.prepare_message(
            conversation_id,
            user_id,
            request_id,
            content,
            model_id=model_id,
            tenant_id=tenant_id,
        )

    async def prepare_approval(
        self,
        conversation_id: UUID,
        user_id: str,
        decisions: Any,
        tenant_id: str | None = None,
    ) -> tuple[PreparedExecution, Any]:
        return await self.execution.prepare_approval(
            conversation_id,
            user_id,
            decisions,
            tenant_id,
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
