"""Web 聊天业务、用户隔离、持久化和 Deep Agents 流式运行服务。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.core.agent import AgentContext, build_research_agent
from melonclaw.core.config import Settings, load_settings
from melonclaw.core.database import (
    BusinessDatabase,
    ConversationBusyError,
    ConversationNotFoundError,
    DatabaseConfigurationError,
    DatabaseSchemaError,
    DatabaseUnavailableError,
    PreparedMessagePair,
    ProjectNotFoundError,
    RequestConflictError,
    RequestRecord,
    UserContext,
    close_memory_store,
    open_checkpoint_pool,
    open_memory_store,
)
from melonclaw.core.hitl import (
    aget_pending_approval,
    build_resume_command,
    serialize_pending_approval,
)
from melonclaw.core.memory import MemoryService
from melonclaw.core.skills import project_skills_enabled
from melonclaw.output.content import content_to_text
from melonclaw.output.events import DISPLAY_EVENT_TYPES, iter_research_events
from melonclaw.output.streaming import _preview, sanitize_text


class InvalidUserError(ValueError):
    """请求中的用户不存在或没有有效的租户标签。"""


class RequestInProgressError(RuntimeError):
    """同一个 request_id 已经在执行中。"""


class AgentExecutionError(RuntimeError):
    """Agent 没有产生可以持久化的最终回复。"""


@dataclass
class PreparedExecution:
    """准备阶段完成后的执行句柄。锁连接会一直持有到流结束。"""

    conversation_id: UUID
    project_id: UUID
    project_name: str
    workdir_path: str
    user_id: str
    tenant_id: str
    tenant_name: str
    tenant_role: str
    tenant_status: str
    request_id: str
    run_id: str
    worker_id: str
    config: dict[str, Any]
    assistant_message_id: UUID
    agent: Any
    lock_connection: AsyncConnection | None = None
    user_message_id: UUID | None = None
    content: str = ""
    resuming: bool = False
    replay_message: dict[str, Any] | None = None
    released: bool = False


@dataclass
class ChatService:
    """持久化聊天服务；不把浏览器 session 当成持久化身份或上下文。"""

    settings: Settings | None = None
    storage: BusinessDatabase | None = None
    checkpoint_pool: AsyncConnectionPool | None = None
    checkpointer: AsyncPostgresSaver | None = None
    memory_store_context: Any | None = None
    memory_store: Any | None = None
    memory_service: MemoryService | None = None
    agent: Any | None = None
    project_agents: dict[str, Any] | None = None
    startup_error: str | None = None
    worker_id: str = field(default_factory=lambda: f"web-{uuid4()}")

    async def initialize(self) -> None:
        """打开两个连接池并构建带 PostgreSQL Checkpointer 的 Agent。"""

        try:
            self.settings = load_settings()
            self.storage = BusinessDatabase(self.settings.database_url)
            await self.storage.open()
            self.memory_store_context, self.memory_store = await open_memory_store(
                self.settings.database_url
            )
            # 初始化和迁移由 melonclaw-db-init 独立执行，服务启动只检查状态。
            await self.storage.verify_schema(
                require_checkpointer=True,
                require_store=True,
            )
            self.memory_service = MemoryService(
                self.storage,
                self.memory_store,
            )
            self.checkpoint_pool = await open_checkpoint_pool(
                self.settings.database_url
            )
            self.checkpointer = AsyncPostgresSaver(self.checkpoint_pool)
            self.agent = await build_research_agent(
                self.settings,
                checkpointer=self.checkpointer,
                memory_service=self.memory_service,
            )
            self.project_agents = {}
        except (DatabaseConfigurationError, DatabaseSchemaError, DatabaseUnavailableError) as exc:
            await self.close()
            self.startup_error = str(exc)
            self.agent = None
        except Exception as exc:  # noqa: BLE001 - 启动错误交给 Web UI 展示
            await self.close()
            self.startup_error = sanitize_text(str(exc))
            self.agent = None

    async def close(self) -> None:
        """按依赖顺序释放 Agent 使用的 Checkpointer 池和业务池。"""

        if self.memory_store_context is not None:
            try:
                await close_memory_store(self.memory_store_context)
            finally:
                self.memory_store_context = None
                self.memory_store = None
                self.memory_service = None
        if self.checkpoint_pool is not None:
            try:
                await self.checkpoint_pool.close()
            finally:
                self.checkpoint_pool = None
                self.checkpointer = None
        if self.storage is not None:
            await self.storage.close()
            self.storage = None
        if self.project_agents is not None:
            self.project_agents.clear()
        self.project_agents = None

    @property
    def ready(self) -> bool:
        return (
            self.agent is not None
            and self.storage is not None
            and self.checkpointer is not None
            and self.memory_store is not None
            and self.memory_service is not None
            and self.startup_error is None
        )

    def status(self) -> dict[str, Any]:
        """返回不含凭据的服务状态。"""

        if self.startup_error:
            state = "error"
        elif self.agent is None:
            state = "starting"
        else:
            state = "ready"
        settings = self.settings
        return {
            "status": state,
            "message": self.startup_error or "",
            "provider": settings.provider if settings else "",
            "model": settings.model_name if settings else "",
            "mcp_servers": sorted(settings.mcp_servers) if settings else [],
            "skills": ["/skills/"] if project_skills_enabled() else [],
            "database": "connected" if self.storage is not None else "",
            "memory_store": "connected" if self.memory_store is not None else "",
        }

    async def resolve_user(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> UserContext:
        """解析用户及可选租户标签；Project/Conversation 只归属用户。"""

        storage = self.storage
        if storage is None:
            raise RuntimeError(self.startup_error or "数据库仍在启动，请稍候。")
        clean_user_id = user_id.strip()
        clean_tenant_id = tenant_id.strip() if tenant_id is not None else None
        if not clean_user_id:
            raise InvalidUserError("user_id 不能为空。")
        context = await storage.get_user_context(clean_user_id, clean_tenant_id)
        if context is None:
            raise InvalidUserError("用户不存在或没有租户标签。")
        await storage.ensure_default_project(
            context.user_id,
        )
        return context

    async def users(self) -> dict[str, Any]:
        storage = self.storage
        if storage is None:
            raise RuntimeError(self.startup_error or "数据库仍在启动，请稍候。")
        return {"items": await storage.list_users()}

    def _require_ready(self) -> tuple[BusinessDatabase, Any]:
        if not self.ready:
            raise RuntimeError(self.startup_error or "Agent 仍在启动，请稍候。")
        assert self.storage is not None
        assert self.agent is not None
        return self.storage, self.agent

    def _project_workspace_dir(self, project: dict[str, Any]) -> Path:
        """把数据库中的受控相对路径解析为 Project 的真实工作目录。"""

        if self.settings is None:
            raise RuntimeError("运行配置尚未加载。")
        root = self.settings.workspace_root.resolve()
        candidate = (root / str(project["workdir_path"])).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise DatabaseSchemaError("Project 工作目录超出 workspace 根目录。") from exc
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    async def _agent_for_project(self, project: dict[str, Any]) -> Any:
        """按 Project 缓存 Agent，使同一 Project 的会话共享文件后端。"""

        self._require_ready()
        if self.settings is None or self.checkpointer is None:
            raise RuntimeError("Agent 仍在启动，请稍候。")
        if self.memory_service is None:
            raise RuntimeError("Memory Store 仍在启动，请稍候。")
        if self.project_agents is None:
            self.project_agents = {}
        key = str(project["id"])
        cached = self.project_agents.get(key)
        if cached is not None:
            return cached
        agent = await build_research_agent(
            self.settings,
            checkpointer=self.checkpointer,
            workspace_dir=self._project_workspace_dir(project),
            memory_service=self.memory_service,
        )
        self.project_agents[key] = agent
        return agent

    async def _project_for_conversation(
        self,
        storage: BusinessDatabase,
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

    @staticmethod
    def _conversation_config(conversation_id: UUID) -> dict[str, Any]:
        return {"configurable": {"thread_id": str(conversation_id)}}

    async def create_project(
        self,
        user_id: str,
        name: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        storage, _ = self._require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        clean_name = " ".join(name.split()).strip()
        if not clean_name:
            raise ValueError("Project 名称不能为空。")
        if len(clean_name) > 120:
            raise ValueError("Project 名称不能超过 120 个字符。")
        project = await storage.create_project(
            context.user_id,
            clean_name,
        )
        self._project_workspace_dir(project)
        return project

    async def list_projects(
        self,
        user_id: str,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        storage, _ = self._require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        return await storage.list_projects(
            context.user_id,
        )

    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        storage, _ = self._require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        if project_id is None:
            # 兼容旧 API：未选择 Project 时，自动使用用户唯一的临时会话。
            project = await storage.ensure_default_project(
                context.user_id,
            )
        else:
            project = await storage.get_project(
                project_id,
                context.user_id,
            )
            if project is None:
                raise ProjectNotFoundError
        self._project_workspace_dir(project)
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
        storage, _ = self._require_ready()
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
        storage, _ = self._require_ready()
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
        project = await self._project_for_conversation(storage, conversation, context)
        agent = await self._agent_for_project(project)
        pending = await aget_pending_approval(
            agent,
            self._conversation_config(conversation_id),
        )
        return {
            "conversation": conversation,
            "items": messages,
            "next_before_seq": next_before_seq,
            "pending_approval": serialize_pending_approval(pending) if pending else None,
        }

    async def prepare_message(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
        content: str,
        tenant_id: str | None = None,
    ) -> PreparedExecution:
        """完成校验、幂等检查、抢锁和短事务消息落库后再返回流。"""

        storage, _ = self._require_ready()
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("消息不能为空。")
        if len(clean_content) > 12000:
            raise ValueError("消息不能超过 12000 个字符。")
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        # Conversation 不绑定 tenant；每轮执行根据请求上下文选择 Tenant Memory。
        context = await self.resolve_user(user_id, tenant_id)
        project = await self._project_for_conversation(storage, conversation, context)
        agent = await self._agent_for_project(project)

        existing = await storage.find_request(
            conversation_id,
            context.user_id,
            request_id,
        )
        if existing is not None:
            return await self._prepare_existing_request(
                conversation_id,
                context,
                request_id,
                clean_content,
                existing,
                agent=agent,
                project=project,
            )

        lock_connection = await storage.try_advisory_lock(conversation_id)
        if lock_connection is None:
            raise ConversationBusyError("当前会话正在处理另一条消息，请稍候。")
        try:
            # 抢锁后再次检查，覆盖两个请求同时完成首次检查的竞态。
            existing = await storage.find_request(
                conversation_id,
                context.user_id,
                request_id,
            )
            if existing is not None:
                return await self._prepare_existing_request(
                    conversation_id,
                    context,
                    request_id,
                    clean_content,
                    existing,
                    lock_connection=lock_connection,
                    agent=agent,
                    project=project,
                )
            if await aget_pending_approval(
                agent,
                self._conversation_config(conversation_id),
            ):
                raise ValueError("当前对话正在等待人工审批，请先处理审批请求。")
            stale = await storage.get_incomplete_assistant(
                conversation_id,
                context.user_id,
            )
            if stale is not None:
                # 能取得会话锁说明旧执行已经不再运行，此时才安全收敛遗留状态。
                await storage.update_assistant(
                    conversation_id,
                    UUID(stale["id"]),
                    status="failed",
                    error_code=(
                        "stale_pending"
                        if stale["status"] == "pending"
                        else "stale_interrupted"
                    ),
                )
            pair = await storage.create_message_pair(
                conversation_id,
                context.user_id,
                request_id,
                clean_content,
            )
            return self._execution_from_pair(
                conversation_id,
                context,
                pair,
                lock_connection,
                agent,
                project,
                run_id=str(uuid4()),
                worker_id=self.worker_id,
            )
        except Exception:
            await storage.release_advisory_lock(lock_connection, conversation_id)
            raise

    async def _prepare_existing_request(
        self,
        conversation_id: UUID,
        context: UserContext,
        request_id: str,
        content: str,
        existing: RequestRecord,
        *,
        lock_connection: AsyncConnection | None = None,
        agent: Any,
        project: dict[str, Any],
    ) -> PreparedExecution:
        storage, _ = self._require_ready()
        if existing.content != content:
            if lock_connection is not None:
                await storage.release_advisory_lock(lock_connection, conversation_id)
            raise RequestConflictError(
                "相同 request_id 已存在，但消息正文与首次请求不同。"
            )
        assistant = existing.assistant_message
        status = assistant["status"]
        if status == "pending":
            if lock_connection is None:
                lock_connection = await storage.try_advisory_lock(conversation_id)
                if lock_connection is None:
                    raise RequestInProgressError("该 request_id 正在执行中，请稍候查询历史。")
            # 没有锁时代表旧进程仍在执行；能拿到锁则确认它已经结束，安全标记失败。
            updated = await storage.update_assistant(
                conversation_id,
                UUID(assistant["id"]),
                status="failed",
                error_code="stale_pending",
            )
            await storage.release_advisory_lock(lock_connection, conversation_id)
            return PreparedExecution(
                conversation_id=conversation_id,
                project_id=UUID(project["id"]),
                project_name=project["name"],
                workdir_path=project["workdir_path"],
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                tenant_name=context.tenant_name_zh,
                tenant_role=context.tenant_role,
                tenant_status=context.tenant_status,
                request_id=request_id,
                run_id="",
                worker_id=self.worker_id,
                config=self._conversation_config(conversation_id),
                assistant_message_id=UUID(updated["id"]),
                agent=agent,
                replay_message=updated,
            )
        if lock_connection is not None:
            # 已完成/失败/取消的幂等重试不需要占用会话锁。
            await storage.release_advisory_lock(lock_connection, conversation_id)
        return PreparedExecution(
            conversation_id=conversation_id,
            project_id=UUID(project["id"]),
            project_name=project["name"],
            workdir_path=project["workdir_path"],
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            tenant_name=context.tenant_name_zh,
            tenant_role=context.tenant_role,
            tenant_status=context.tenant_status,
            request_id=request_id,
            run_id="",
            worker_id=self.worker_id,
            config=self._conversation_config(conversation_id),
            assistant_message_id=UUID(assistant["id"]),
            agent=agent,
            replay_message=assistant,
        )

    @staticmethod
    def _execution_from_pair(
        conversation_id: UUID,
        context: UserContext,
        pair: PreparedMessagePair,
        lock_connection: AsyncConnection,
        agent: Any,
        project: dict[str, Any],
        *,
        run_id: str,
        worker_id: str,
    ) -> PreparedExecution:
        return PreparedExecution(
            conversation_id=conversation_id,
            project_id=UUID(project["id"]),
            project_name=project["name"],
            workdir_path=project["workdir_path"],
            user_id=context.user_id,
            tenant_id=context.tenant_id,
            tenant_name=context.tenant_name_zh,
            tenant_role=context.tenant_role,
            tenant_status=context.tenant_status,
            request_id=pair.request_id,
            run_id=run_id,
            worker_id=worker_id,
            config=ChatService._conversation_config(conversation_id),
            assistant_message_id=UUID(pair.assistant_message["id"]),
            agent=agent,
            user_message_id=UUID(pair.user_message["id"]),
            content=pair.user_message["content"],
            lock_connection=lock_connection,
        )

    async def prepare_approval(
        self,
        conversation_id: UUID,
        user_id: str,
        decisions: Any,
        tenant_id: str | None = None,
    ) -> tuple[PreparedExecution, Any]:
        storage, _ = self._require_ready()
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        # 审批恢复同样按当前请求校验租户成员关系，但不改变 Conversation 归属。
        context = await self.resolve_user(user_id, tenant_id)
        project = await self._project_for_conversation(storage, conversation, context)
        agent = await self._agent_for_project(project)
        pending = await aget_pending_approval(
            agent,
            self._conversation_config(conversation_id),
        )
        if pending is None:
            raise ValueError("当前没有等待处理的审批请求。")
        assistant = await storage.get_incomplete_assistant(
            conversation_id,
            context.user_id,
        )
        if assistant is None:
            raise ValueError("找不到等待审批的业务消息记录。")
        lock_connection = await storage.try_advisory_lock(conversation_id)
        if lock_connection is None:
            raise ConversationBusyError("当前会话正在处理另一条消息，请稍候。")
        try:
            command = build_resume_command(pending, decisions)
        except Exception:
            await storage.release_advisory_lock(lock_connection, conversation_id)
            raise
        return (
            PreparedExecution(
                conversation_id=conversation_id,
                project_id=UUID(project["id"]),
                project_name=project["name"],
                workdir_path=project["workdir_path"],
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                tenant_name=context.tenant_name_zh,
                tenant_role=context.tenant_role,
                tenant_status=context.tenant_status,
                request_id=assistant["request_id"],
                run_id=str(uuid4()),
                worker_id=self.worker_id,
                config=self._conversation_config(conversation_id),
                assistant_message_id=UUID(assistant["id"]),
                agent=agent,
                lock_connection=lock_connection,
                content=assistant["content"],
                resuming=True,
            ),
            command,
        )

    async def stream_execution(
        self,
        execution: PreparedExecution,
        *,
        agent_input: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """发送业务事件；数据库最终状态先提交，再发送 completed。"""

        storage, _ = self._require_ready()
        agent = execution.agent
        display_events: list[dict[str, Any]] = []
        emitted_text: list[str] = []
        finished = False

        def remember_display_event(event: dict[str, Any]) -> None:
            """保留可回放的工具/子 Agent轨迹，并合并子 Agent 文本分片。"""

            event_type = event.get("type")
            if event_type not in DISPLAY_EVENT_TYPES:
                return
            if event_type == "subagent_text":
                subagent_id = event.get("subagent_id")
                for previous in reversed(display_events):
                    if (
                        previous.get("type") == "subagent_text"
                        and previous.get("subagent_id") == subagent_id
                    ):
                        previous["text"] = _preview(
                            f"{previous.get('text', '')}{event.get('text', '')}",
                            limit=12000,
                        )
                        return
            display_events.append(dict(event))

        try:
            yield {
                "type": "message_started",
                "conversation_id": str(execution.conversation_id),
                "request_id": execution.request_id,
                "user_message_id": str(execution.user_message_id) if execution.user_message_id else None,
                "message_id": str(execution.assistant_message_id),
                "resuming": execution.resuming,
            }
            if execution.replay_message is not None:
                replay = execution.replay_message
                if replay["status"] == "completed":
                    yield {
                        "type": "completed",
                        "message_id": replay["id"],
                        "request_id": execution.request_id,
                        "content": replay["content"],
                        "replayed": True,
                    }
                else:
                    yield {
                        "type": "message_status",
                        "message_id": replay["id"],
                        "request_id": execution.request_id,
                        "status": replay["status"],
                        "error_code": replay.get("error_code"),
                        "replayed": True,
                    }
                yield {"type": "done", "replayed": True}
                finished = True
                return

            if agent_input is None:
                agent_input = {
                    "messages": [
                        {
                            "id": str(execution.user_message_id),
                            "role": "user",
                            "content": execution.content,
                        }
                    ]
                }

            async for event in iter_research_events(
                agent,
                agent_input,
                execution.config,
                context=AgentContext(
                    user_id=execution.user_id,
                    tenant_id=execution.tenant_id,
                    tenant_name=execution.tenant_name,
                    project_id=str(execution.project_id),
                    project_name=execution.project_name,
                    workdir_path=execution.workdir_path,
                    request_id=execution.request_id,
                    run_id=execution.run_id,
                    worker_id=execution.worker_id,
                    tenant_role=execution.tenant_role,
                    tenant_status=execution.tenant_status,
                    memory_enabled=True,
                ),
            ):
                if event.get("type") == "text":
                    emitted_text.append(str(event.get("text", "")))
                remember_display_event(event)
                yield event

            pending = await aget_pending_approval(agent, execution.config)
            display_metadata = {"events": display_events} if display_events else {}
            if pending:
                await storage.update_assistant(
                    execution.conversation_id,
                    execution.assistant_message_id,
                    status="interrupted",
                    display_metadata=display_metadata,
                )
                yield {
                    "type": "approval_required",
                    "request": serialize_pending_approval(pending),
                }
                finished = True
                return

            final_content = await self._latest_root_assistant_text(
                agent,
                execution.config,
            )
            if not final_content:
                final_content = "".join(emitted_text).strip()
            if not final_content:
                raise AgentExecutionError("Agent 未返回可保存的助手回复。")
            saved = await storage.update_assistant(
                execution.conversation_id,
                execution.assistant_message_id,
                content=final_content,
                status="completed",
                display_metadata=display_metadata,
            )
            yield {
                "type": "completed",
                "message_id": saved["id"],
                "request_id": execution.request_id,
                "content": saved["content"],
            }
            yield {"type": "done", "message_id": saved["id"]}
            finished = True
        except asyncio.CancelledError:
            await self._mark_execution(
                execution,
                status="cancelled",
                error_code="request_cancelled",
                display_metadata=display_events,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 保存失败状态后发送安全错误
            await self._mark_execution(
                execution,
                status="failed",
                error_code="agent_execution_failed",
                display_metadata=display_events,
            )
            yield {
                "type": "error",
                "message": sanitize_text(str(exc)) or "助手运行失败，请稍后重试。",
                "message_id": str(execution.assistant_message_id),
            }
        finally:
            if not finished and execution.replay_message is None:
                # 连接在未完成流的异常路径也必须释放；状态已在上面的异常分支处理。
                pass
            await self._release_execution(execution)

    async def _mark_execution(
        self,
        execution: PreparedExecution,
        *,
        status: str,
        error_code: str,
        display_metadata: list[dict[str, Any]],
    ) -> None:
        if self.storage is None:
            return
        try:
            await self.storage.update_assistant(
                execution.conversation_id,
                execution.assistant_message_id,
                status=status,
                display_metadata={"events": display_metadata} if display_metadata else {},
                error_code=error_code,
            )
        except Exception:  # noqa: BLE001 - 不覆盖原始 Agent/取消错误
            # 原始 Agent/取消错误优先；下一次历史查询仍会显示已存在的业务状态。
            return

    async def _release_execution(self, execution: PreparedExecution) -> None:
        if execution.released or execution.lock_connection is None:
            execution.released = True
            return
        execution.released = True
        if self.storage is not None:
            await self.storage.release_advisory_lock(
                execution.lock_connection,
                execution.conversation_id,
            )

    async def _latest_root_assistant_text(
        self,
        agent: Any,
        config: dict[str, Any],
    ) -> str:
        snapshot = await agent.aget_state(config)
        values = getattr(snapshot, "values", {}) or {}
        for message in reversed(values.get("messages", []) or []):
            message_type = getattr(message, "type", None)
            if isinstance(message, dict):
                message_type = message.get("role") or message.get("type")
                content = message.get("content", "")
                tool_calls = message.get("tool_calls")
            else:
                content = getattr(message, "content", "")
                tool_calls = getattr(message, "tool_calls", None)
            if message_type in {"ai", "assistant"} and not tool_calls:
                text = content_to_text(content)
                if text.strip():
                    return text.strip()
        return ""


__all__ = [
    "ChatService",
    "ConversationBusyError",
    "ConversationNotFoundError",
    "InvalidUserError",
    "RequestConflictError",
    "RequestInProgressError",
]
