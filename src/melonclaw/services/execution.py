"""Agent 消息执行、HITL 恢复和执行事件流。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.core.agent import AgentContext
from melonclaw.core.hitl import (
    aget_pending_approval,
    build_resume_command,
    serialize_pending_approval,
)
from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.output.content import content_to_text
from melonclaw.output.events import DISPLAY_EVENT_TYPES, iter_research_events
from melonclaw.output.formatting import _preview, sanitize_text
from melonclaw.repository import (
    AssistantStateConflictError,
    ConversationBusyError,
    ConversationNotFoundError,
    PreparedMessagePair,
    RequestConflictError,
    RequestRecord,
    UserContext,
)
from melonclaw.services.errors import (
    AgentExecutionError,
    RequestInProgressError,
)
from melonclaw.services.runtime import ChatRuntime
from melonclaw.services.conversations import ConversationService


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
    model: ResolvedModel
    lock_connection: AsyncConnection | None = None
    user_message_id: UUID | None = None
    content: str = ""
    resuming: bool = False
    replay_message: dict[str, Any] | None = None
    released: bool = False


class ExecutionService:
    """处理消息幂等、会话锁、Agent 执行和最终状态落库。"""

    def __init__(self, runtime: ChatRuntime, conversations: ConversationService) -> None:
        self.runtime = runtime
        self.conversations = conversations

    async def prepare_message(
        self,
        conversation_id: UUID,
        user_id: str,
        request_id: str,
        content: str,
        model_id: str | None = None,
        tenant_id: str | None = None,
    ) -> PreparedExecution:
        """完成校验、幂等检查、抢锁和短事务消息落库后再返回流。"""

        storage = self.runtime.require_ready()
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("消息不能为空。")
        if len(clean_content) > 12000:
            raise ValueError("消息不能超过 12000 个字符。")
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        # Conversation 不绑定 tenant；每轮执行根据请求上下文选择 Tenant Memory。
        context = await self.conversations.resolve_user(user_id, tenant_id)
        project = await self.conversations.project_for_conversation(
            storage,
            conversation,
            context,
        )

        existing = await storage.find_request(
            conversation_id,
            context.user_id,
            request_id,
        )
        if existing is not None:
            # 幂等重试沿用第一次请求实际绑定的模型，即使前端下拉框已经
            # 切换到了另一个选项。
            model = self.runtime.model_for_message(existing.assistant_message)
            agent = await self.runtime.agent_for_project(project, model)
            return await self._prepare_existing_request(
                conversation_id,
                context,
                request_id,
                clean_content,
                existing,
                agent=agent,
                project=project,
                model=model,
            )

        model = self.runtime.resolve_model(model_id)
        agent = await self.runtime.agent_for_project(project, model)
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
                stored_model = self.runtime.model_for_message(
                    existing.assistant_message
                )
                stored_agent = await self.runtime.agent_for_project(
                    project,
                    stored_model,
                )
                return await self._prepare_existing_request(
                    conversation_id,
                    context,
                    request_id,
                    clean_content,
                    existing,
                    lock_connection=lock_connection,
                    agent=stored_agent,
                    project=project,
                    model=stored_model,
                )
            if await aget_pending_approval(
                agent,
                self.runtime.conversation_config(conversation_id),
            ):
                raise ValueError("当前对话正在等待人工审批，请先处理审批请求。")
            stale = await storage.get_incomplete_assistant(
                conversation_id,
                context.user_id,
            )
            if stale is not None:
                # 能取得会话锁说明旧执行已经不再运行，此时才安全收敛遗留状态。
                try:
                    await storage.update_assistant(
                        conversation_id,
                        UUID(stale["id"]),
                        status="failed",
                        error_code=(
                            "stale_pending"
                            if stale["status"] == "pending"
                            else "stale_interrupted"
                        ),
                        expected_status=stale["status"],
                    )
                except AssistantStateConflictError:
                    # 状态已经由持锁执行收敛；不要用旧快照覆盖它。
                    pass
            pair = await storage.create_message_pair(
                conversation_id,
                context.user_id,
                request_id,
                clean_content,
                model_id=model.profile_id,
                model_provider=model.provider,
                model_name=model.model_name,
                model_display_name=model.display_name,
            )
            return self._execution_from_pair(
                conversation_id,
                context,
                pair,
                lock_connection,
                agent,
                project,
                model,
                run_id=str(uuid4()),
                worker_id=self.runtime.worker_id,
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
        model: ResolvedModel,
        project: dict[str, Any],
    ) -> PreparedExecution:
        storage = self.runtime.require_ready()
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
            try:
                # 初次读取到 pending 只代表一个瞬间。拿到锁后必须重新读，
                # 因为原执行可能刚好已经提交 completed/failed。
                latest = await storage.find_request(
                    conversation_id,
                    context.user_id,
                    request_id,
                )
                if latest is None:
                    raise ConversationNotFoundError
                if latest.content != content:
                    raise RequestConflictError(
                        "相同 request_id 已存在，但消息正文与首次请求不同。"
                    )
                assistant = latest.assistant_message
                status = assistant["status"]
                if status == "pending":
                    try:
                        assistant = await storage.update_assistant(
                            conversation_id,
                            UUID(assistant["id"]),
                            status="failed",
                            error_code="stale_pending",
                            expected_status="pending",
                        )
                    except AssistantStateConflictError:
                        # 即使锁外存在异常旧执行，CAS 失败后也只回放最新状态。
                        refreshed = await storage.find_request(
                            conversation_id,
                            context.user_id,
                            request_id,
                        )
                        if refreshed is None:
                            raise ConversationNotFoundError
                        assistant = refreshed.assistant_message
                        if assistant["status"] == "pending":
                            raise
            finally:
                await storage.release_advisory_lock(lock_connection, conversation_id)
            return self._replay_execution(
                conversation_id,
                context,
                assistant,
                agent,
                model,
                project,
            )
        if lock_connection is not None:
            # 已完成/失败/取消的幂等重试不需要占用会话锁。
            await storage.release_advisory_lock(lock_connection, conversation_id)
        return self._replay_execution(
            conversation_id,
            context,
            assistant,
            agent,
            model,
            project,
        )

    def _replay_execution(
        self,
        conversation_id: UUID,
        context: UserContext,
        assistant: dict[str, Any],
        agent: Any,
        model: ResolvedModel,
        project: dict[str, Any],
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
            request_id=assistant["request_id"],
            run_id="",
            worker_id=self.runtime.worker_id,
            config=self.runtime.conversation_config(conversation_id),
            assistant_message_id=UUID(assistant["id"]),
            agent=agent,
            model=model,
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
        model: ResolvedModel,
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
            config=ChatRuntime.conversation_config(conversation_id),
            assistant_message_id=UUID(pair.assistant_message["id"]),
            agent=agent,
            model=model,
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
        storage = self.runtime.require_ready()
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        # 审批恢复同样按当前请求校验租户成员关系，但不改变 Conversation 归属。
        context = await self.conversations.resolve_user(user_id, tenant_id)
        project = await self.conversations.project_for_conversation(
            storage,
            conversation,
            context,
        )
        assistant = await storage.get_incomplete_assistant(
            conversation_id,
            context.user_id,
        )
        if assistant is None:
            raise ValueError("找不到等待审批的业务消息记录。")
        model = self.runtime.model_for_message(assistant)
        agent = await self.runtime.agent_for_project(project, model)
        pending = await aget_pending_approval(
            agent,
            self.runtime.conversation_config(conversation_id),
        )
        if pending is None:
            raise ValueError("当前没有等待处理的审批请求。")
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
                worker_id=self.runtime.worker_id,
                config=self.runtime.conversation_config(conversation_id),
                assistant_message_id=UUID(assistant["id"]),
                agent=agent,
                model=model,
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

        storage = self.runtime.require_ready()
        agent = execution.agent
        display_events: list[dict[str, Any]] = []
        emitted_text: list[str] = []
        finished = False

        def remember_display_event(event: dict[str, Any]) -> None:
            """保留可回放的工具/子 Agent 轨迹，并合并子 Agent 文本分片。"""

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
                "user_message_id": (
                    str(execution.user_message_id)
                    if execution.user_message_id
                    else None
                ),
                "message_id": str(execution.assistant_message_id),
                "resuming": execution.resuming,
                "model": execution.model.public_dict(),
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
                    model_id=execution.model.profile_id,
                    model_spec=execution.model.model_spec,
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
                    expected_status=self._expected_assistant_status(execution),
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
                expected_status=self._expected_assistant_status(execution),
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
        storage = self.runtime.storage
        if storage is None:
            return
        try:
            await storage.update_assistant(
                execution.conversation_id,
                execution.assistant_message_id,
                status=status,
                display_metadata={"events": display_metadata} if display_metadata else {},
                error_code=error_code,
                expected_status=self._expected_assistant_status(execution),
            )
        except Exception:  # noqa: BLE001 - 不覆盖原始 Agent/取消错误
            # 原始 Agent/取消错误优先；下一次历史查询仍会显示已存在的业务状态。
            return

    async def _release_execution(self, execution: PreparedExecution) -> None:
        if execution.released or execution.lock_connection is None:
            execution.released = True
            return
        execution.released = True
        storage = self.runtime.storage
        if storage is not None:
            await storage.release_advisory_lock(
                execution.lock_connection,
                execution.conversation_id,
            )

    @staticmethod
    def _expected_assistant_status(execution: PreparedExecution) -> str:
        """返回本次执行被允许推进的状态，作为数据库 CAS 条件。"""

        return "interrupted" if execution.resuming else "pending"

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
