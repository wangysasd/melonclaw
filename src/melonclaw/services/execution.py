"""Agent 消息执行、HITL 恢复和执行事件流。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.core.agent import AgentContext
from melonclaw.core.hitl import (
    aget_pending_approval,
    aget_pending_interaction,
    approval_batch_id_for,
    build_resume_command,
    serialize_pending_approval,
    serialize_pending_user_question,
)
from melonclaw.core.model_catalog import ResolvedModel
from melonclaw.core.user_input import normalize_capabilities
from melonclaw.output.assistant_steps import AssistantStepAccumulator, events_from_snapshot
from melonclaw.output.content import content_to_text
from melonclaw.output.events import DISPLAY_EVENT_TYPES, iter_research_events
from melonclaw.output.formatting import _preview, sanitize_text
from melonclaw.output.visible_text import visible_text
from melonclaw.repository import (
    ApprovalBindingError,
    AssistantStateConflictError,
    AttachmentStateError,
    ConversationBusyError,
    ConversationNotFoundError,
    PreparedMessagePair,
    RequestConflictError,
    RequestRecord,
    UserContext,
)
from melonclaw.services.attachments import AttachmentService
from melonclaw.services.conversations import ConversationService
from melonclaw.services.errors import (
    AgentExecutionError,
    RequestInProgressError,
)
from melonclaw.services.execution_finalize import (
    mark_execution_status,
    mark_interaction_recovery_required,
    release_execution,
)
from melonclaw.services.runtime import ChatRuntime


@dataclass
class PreparedExecution:
    """准备阶段完成后的执行句柄。锁连接会一直持有到流结束。"""

    conversation_id: UUID
    project_id: UUID | None
    project_name: str | None
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
    agent: Any | None
    model: ResolvedModel
    lock_connection: AsyncConnection | None = None
    user_message_id: UUID | None = None
    content: str = ""
    skill_id: str | None = None
    user_interaction_id: UUID | None = None
    resuming: bool = False
    replay_message: dict[str, Any] | None = None
    assistant_steps: list[dict[str, Any]] = field(default_factory=list)
    execution_duration_ms: int | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    released: bool = False


class ExecutionService:
    """处理消息幂等、会话锁、Agent 执行和最终状态落库。"""
    def __init__(
        self,
        runtime: ChatRuntime,
        conversations: ConversationService,
        *,
        attachments: AttachmentService | None = None,
    ) -> None:
        self.runtime = runtime
        self.conversations = conversations
        self.attachments = attachments or AttachmentService(runtime, conversations)
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
        """完成校验、幂等检查、抢锁和短事务消息落库后再返回流。"""

        storage = self.runtime.require_ready()
        normalized_capabilities = normalize_capabilities(capabilities)
        clean_content = content.strip()
        try:
            normalized_attachment_ids = [UUID(str(item)) for item in (attachment_ids or [])]
        except (TypeError, ValueError) as exc:
            raise AttachmentStateError("附件 ID 无效。", "invalid_attachment_id") from exc
        if not clean_content and not normalized_attachment_ids:
            raise AttachmentStateError("消息正文和附件不能同时为空。", "message_empty")
        if len(clean_content) > 12000:
            raise ValueError("消息不能超过 12000 个字符。")
        selected_skill = await self.runtime.resolve_skill(user_id, skill_id)
        if skill_id and selected_skill is None:
            raise ValueError("选择的技能不存在或已被移除。")
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        context = await self.conversations.resolve_user(user_id)
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
            # 幂等重试沿用第一次请求实际绑定的模型和客户端能力。
            model = await self.runtime.model_for_message(
                context.user_id, existing.assistant_message
            )
            return await self._prepare_existing_request(
                conversation_id,
                context,
                request_id,
                clean_content,
                existing,
                skill_id=skill_id,
                attachment_ids=normalized_attachment_ids,
                conversation=conversation,
                project=project,
                model=model,
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
                stored_model = await self.runtime.model_for_message(
                    context.user_id, existing.assistant_message
                )
                # 该分支的锁由 _prepare_existing_request 接管，包括异常释放。
                existing_lock = lock_connection
                lock_connection = None
                return await self._prepare_existing_request(
                    conversation_id,
                    context,
                    request_id,
                    clean_content,
                    existing,
                    skill_id=skill_id,
                    attachment_ids=normalized_attachment_ids,
                    lock_connection=existing_lock,
                    conversation=conversation,
                    project=project,
                    model=stored_model,
                )
            model = await self.runtime.resolve_model(context.user_id, model_id)
            agent = await self.runtime.agent_for_conversation(
                conversation, project, model, normalized_capabilities
            )
            if await aget_pending_interaction(
                agent,
                self.runtime.conversation_config(conversation_id),
            ):
                raise ValueError("当前对话正在等待人工交互，请先处理待处理请求。")
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
            display_metadata: dict[str, Any] | None = None
            if selected_skill is not None:
                pinned = next((item for item in agent.melonclaw_skill_references if item["id"] == selected_skill.key), None)
                if pinned is None:
                    raise ValueError("选择的技能已不可用，请重新选择。")
                display_metadata = {"skill": pinned}
            display_metadata = display_metadata or {}
            display_metadata["skill_catalog"] = agent.melonclaw_skill_references
            if normalized_capabilities:
                # 能力随消息落库：恢复执行要沿用提问那一轮的同一份能力。
                display_metadata = display_metadata or {}
                display_metadata["capabilities"] = list(normalized_capabilities)
            if self.runtime.settings is None:
                raise RuntimeError("运行配置尚未加载。")
            pair = await storage.create_message_pair(
                conversation_id,
                context.user_id,
                UUID(project["id"]) if project is not None else None,
                request_id,
                clean_content,
                attachment_ids=normalized_attachment_ids,
                model_id=model.profile_id,
                model_provider=model.provider,
                model_name=model.model_name,
                model_display_name=model.display_name,
                model_supports_image="image" in model.input_modalities,
                max_attachment_count=self.runtime.settings.attachment_max_per_message,
                max_total_bytes=self.runtime.settings.attachment_max_total_bytes,
                user_display_metadata=display_metadata,
            )
            return self._execution_from_pair(
                conversation_id,
                context,
                pair,
                lock_connection,
                agent,
                conversation,
                project,
                model,
                run_id=str(uuid4()),
                worker_id=self.runtime.worker_id,
                skill_id=selected_skill.key if selected_skill is not None else None,
                attachments=list(pair.attachments),
            )
        except BaseException:
            # Agent 构建也在持锁区内；请求取消同样必须释放连接级会话锁。
            if lock_connection is not None:
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
        skill_id: str | None,
        attachment_ids: list[UUID],
        lock_connection: AsyncConnection | None = None,
        model: ResolvedModel,
        conversation: dict[str, Any],
        project: dict[str, Any] | None,
    ) -> PreparedExecution:
        storage = self.runtime.require_ready()
        if (
            existing.content != content
            or self._stored_skill_id(existing.user_message) != skill_id
            or tuple(str(item) for item in attachment_ids) != existing.attachment_ids
        ):
            if lock_connection is not None:
                await storage.release_advisory_lock(lock_connection, conversation_id)
            raise RequestConflictError(
                "相同 request_id 已存在，但消息正文、技能或附件选择与首次请求不同。"
            )
        assistant = existing.assistant_message
        status = assistant["status"]
        if status == "pending":
            if lock_connection is None:
                lock_connection = await storage.try_advisory_lock(conversation_id)
                if lock_connection is None:
                    raise RequestInProgressError("该 request_id 正在执行中，请稍候查询历史。")
            try:
                # 拿到锁后重新读取，避免原执行刚好已提交终态。
                latest = await storage.find_request(
                    conversation_id,
                    context.user_id,
                    request_id,
                )
                if latest is None:
                    raise ConversationNotFoundError
                if (
                    latest.content != content
                    or self._stored_skill_id(latest.user_message) != skill_id
                    or tuple(str(item) for item in attachment_ids) != latest.attachment_ids
                ):
                    raise RequestConflictError(
                        "相同 request_id 已存在，但消息正文、技能或附件选择与首次请求不同。"
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
                model,
                conversation,
                project,
                skill_id=skill_id,
                attachments=list(existing.attachments),
            )
        if lock_connection is not None:
            # 已完成/失败/取消的幂等重试不需要占用会话锁。
            await storage.release_advisory_lock(lock_connection, conversation_id)
        return self._replay_execution(
            conversation_id,
            context,
            assistant,
            model,
            conversation,
            project,
            skill_id=skill_id,
            attachments=list(existing.attachments),
        )

    @staticmethod
    def _stored_skill_id(user_message: dict[str, Any]) -> str | None:
        metadata = user_message.get("display_metadata")
        if not isinstance(metadata, dict):
            return None
        selected = metadata.get("skill")
        if not isinstance(selected, dict):
            return None
        value = selected.get("id")
        return value if isinstance(value, str) and value else None

    def _replay_execution(
        self,
        conversation_id: UUID,
        context: UserContext,
        assistant: dict[str, Any],
        model: ResolvedModel,
        conversation: dict[str, Any],
        project: dict[str, Any] | None,
        *,
        skill_id: str | None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PreparedExecution:
        return PreparedExecution(
            conversation_id=conversation_id,
            project_id=UUID(project["id"]) if project is not None else None,
            project_name=project["name"] if project is not None else None,
            workdir_path=str(self.runtime.workspace_dir(conversation, project)),
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
            agent=None,
            model=model,
            skill_id=skill_id,
            replay_message=assistant,
            assistant_steps=list(assistant["assistant_steps"]),
            attachments=attachments or [],
        )

    def _execution_from_pair(
        self,
        conversation_id: UUID,
        context: UserContext,
        pair: PreparedMessagePair,
        lock_connection: AsyncConnection,
        agent: Any,
        conversation: dict[str, Any],
        project: dict[str, Any] | None,
        model: ResolvedModel,
        *,
        run_id: str,
        worker_id: str,
        skill_id: str | None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> PreparedExecution:
        return PreparedExecution(
            conversation_id=conversation_id,
            project_id=UUID(project["id"]) if project is not None else None,
            project_name=project["name"] if project is not None else None,
            workdir_path=str(self.runtime.workspace_dir(conversation, project)),
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
            skill_id=skill_id,
            lock_connection=lock_connection,
            attachments=attachments or [],
            assistant_steps=list(pair.assistant_message["assistant_steps"]),
        )

    async def prepare_approval(
        self,
        conversation_id: UUID,
        user_id: str,
        decisions: Any,
        *,
        approval_batch_id: UUID,
        assistant_message_id: UUID,
    ) -> tuple[PreparedExecution, Any]:
        storage = self.runtime.require_ready()
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        context = await self.conversations.resolve_user(user_id)
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
        if str(assistant["id"]) != str(assistant_message_id):
            raise ApprovalBindingError
        request_record = await storage.find_request(
            conversation_id,
            context.user_id,
            assistant["request_id"],
        )
        model = await self.runtime.model_for_message(context.user_id, assistant)
        agent = await self.runtime.agent_for_conversation(
            conversation,
            project,
            model,
            self.runtime.capabilities_for_message(
                request_record.user_message if request_record is not None else None
            ),
        )
        pending = await aget_pending_approval(
            agent,
            self.runtime.conversation_config(conversation_id),
        )
        if pending is None:
            raise ValueError("当前没有等待处理的审批请求。")
        expected_batch_id = approval_batch_id_for(
            pending,
            assistant_message_id=str(assistant["id"]),
        )
        if str(approval_batch_id) != expected_batch_id:
            raise ApprovalBindingError
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
                project_id=UUID(project["id"]) if project is not None else None,
                project_name=project["name"] if project is not None else None,
                workdir_path=str(self.runtime.workspace_dir(conversation, project)),
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
                attachments=(
                    list(request_record.attachments)
                    if request_record is not None
                    else []
                ),
                assistant_steps=list(assistant["assistant_steps"]),
                execution_duration_ms=assistant["execution_duration_ms"],
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
        transcript = AssistantStepAccumulator(
            message_id=str(execution.assistant_message_id),
            run_id=execution.run_id or str(execution.assistant_message_id),
            steps=execution.assistant_steps,
        )
        started_at = perf_counter()
        base_duration_ms = execution.execution_duration_ms or 0
        finished = False

        def elapsed_duration_ms() -> int:
            return base_duration_ms + int((perf_counter() - started_at) * 1000)

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
                "attachments": execution.attachments,
            }
            if execution.replay_message is not None:
                replay = execution.replay_message
                for replay_event in events_from_snapshot(
                    replay["id"], replay["assistant_steps"]
                ):
                    yield replay_event
                if replay["status"] == "completed":
                    yield {
                        "type": "completed",
                        "message_id": replay["id"],
                        "request_id": execution.request_id,
                        "content": replay["content"],
                        "assistant_steps": replay["assistant_steps"],
                        "execution_duration_ms": replay.get("execution_duration_ms"),
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

            if agent is None:
                raise AgentExecutionError("执行 Agent 尚未初始化。")

            if agent_input is None:
                messages: list[dict[str, str]] = []
                if execution.skill_id:
                    skill = await self.runtime.resolve_skill(
                        execution.user_id, execution.skill_id
                    )
                    if skill is None:
                        # 用户重新选一个技能就能继续，不是服务端故障。
                        raise AgentExecutionError(
                            "选择的技能已不可用，请重新选择技能后重试。",
                            status_code=409,
                        )
                    pinned = next((item for item in agent.melonclaw_skill_references if item["id"] == execution.skill_id), None)
                    if pinned is None or pinned["content_hash"] != skill.content_hash:
                        raise AgentExecutionError("技能内容已更新，请重新发送消息。", status_code=409)
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "本轮用户通过技能选择器明确选择了一个技能。请优先读取并遵守 "
                                f"{skill.virtual_path} 中的 SKILL.md；不要向用户暴露该内部路径。"
                            ),
                        }
                    )
                messages.append(
                    {
                        "id": str(execution.user_message_id),
                        "role": "user",
                        "content": execution.content,
                    }
                )
                agent_input = {"messages": messages}

            async for event in iter_research_events(
                agent,
                agent_input,
                execution.config,
                context=AgentContext(
                    user_id=execution.user_id,
                    conversation_id=str(execution.conversation_id),
                    tenant_id=execution.tenant_id,
                    tenant_name=execution.tenant_name,
                    project_id=str(execution.project_id) if execution.project_id else "",
                    project_name=execution.project_name or "",
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
                assistant_message_id=str(execution.assistant_message_id),
                run_id=execution.run_id,
                projector=transcript,
            ):
                remember_display_event(event)
                yield event

            pending = await aget_pending_interaction(agent, execution.config)
            display_metadata = {"events": display_events} if display_events else {}
            if execution.user_interaction_id is not None:
                await storage.resolve_user_interaction(
                    execution.conversation_id,
                    execution.user_interaction_id,
                )
            if pending:
                user_questions = [
                    item for item in pending if item.get("kind") == "user_question"
                ]
                if user_questions:
                    if len(pending) != 1:
                        raise AgentExecutionError("一次执行只能等待一张用户问题卡片。")
                    settings = self.runtime.settings
                    if settings is None:
                        raise AgentExecutionError("运行配置尚未加载。")
                    interaction = await storage.create_or_get_user_interaction(
                        execution.conversation_id,
                        execution.assistant_message_id,
                        execution.user_id,
                        str(user_questions[0]["id"]),
                        user_questions[0],
                        settings.user_input_ttl_seconds,
                    )
                    question = serialize_pending_user_question(
                        user_questions[0],
                        interaction_id=interaction["id"],
                        assistant_message_id=str(execution.assistant_message_id),
                        expires_at=interaction["expires_at"],
                    )
                    display_metadata["pending_interaction"] = question
                    await storage.update_assistant(
                        execution.conversation_id,
                        execution.assistant_message_id,
                        status="interrupted",
                        assistant_steps=transcript.terminal_snapshot(status="interrupted"),
                        execution_duration_ms=elapsed_duration_ms(),
                        display_metadata=display_metadata,
                        expected_status=self._expected_assistant_status(execution),
                    )
                    yield {"type": "user_input_required", "request": question}
                    finished = True
                    return
                approval_batch_id = approval_batch_id_for(
                    pending,
                    assistant_message_id=str(execution.assistant_message_id),
                )
                approval = serialize_pending_approval(
                    pending,
                    approval_batch_id=approval_batch_id,
                    assistant_message_id=str(execution.assistant_message_id),
                )
                display_metadata["pending_approval"] = {
                    "approval_batch_id": approval_batch_id,
                    "assistant_message_id": str(execution.assistant_message_id),
                }
                await storage.update_assistant(
                    execution.conversation_id,
                    execution.assistant_message_id,
                    status="interrupted",
                    assistant_steps=transcript.terminal_snapshot(status="interrupted"),
                    execution_duration_ms=elapsed_duration_ms(),
                    display_metadata=display_metadata,
                    expected_status=self._expected_assistant_status(execution),
                )
                yield {
                    "type": "approval_required",
                    "request": approval,
                }
                finished = True
                return

            final_content = await self._latest_root_assistant_text(
                agent,
                execution.config,
            )
            if not final_content:
                final_content = transcript.visible_content().strip()
            final_content = sanitize_text(final_content)
            if not final_content:
                raise AgentExecutionError("Agent 未返回可保存的助手回复。")
            assistant_steps = transcript.terminal_snapshot(
                status="completed",
                final_content=final_content,
            )
            saved = await storage.update_assistant(
                execution.conversation_id,
                execution.assistant_message_id,
                content=final_content,
                status="completed",
                assistant_steps=assistant_steps,
                execution_duration_ms=elapsed_duration_ms(),
                display_metadata=display_metadata,
                expected_status=self._expected_assistant_status(execution),
            )
            yield {
                "type": "completed",
                "message_id": saved["id"],
                "request_id": execution.request_id,
                "content": saved["content"],
                # completed 是终态对账帧：即使浏览器漏掉了前面的增量事件，
                # 也能用数据库已提交的完整快照恢复中间 AIMessage 和工具调用。
                "assistant_steps": saved["assistant_steps"],
                "execution_duration_ms": saved.get("execution_duration_ms"),
            }
            yield {"type": "done", "message_id": saved["id"]}
            finished = True
        except asyncio.CancelledError:
            await mark_interaction_recovery_required(
                self.runtime.storage,
                execution.conversation_id,
                execution.user_interaction_id,
            )
            await mark_execution_status(
                self.runtime.storage,
                execution,
                expected_status=self._expected_assistant_status(execution),
                status="cancelled",
                error_code="request_cancelled",
                display_metadata=display_events,
                assistant_steps=transcript.terminal_snapshot(status="cancelled"),
                execution_duration_ms=elapsed_duration_ms(),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 保存失败状态后发送安全错误
            await mark_interaction_recovery_required(
                self.runtime.storage,
                execution.conversation_id,
                execution.user_interaction_id,
            )
            await mark_execution_status(
                self.runtime.storage,
                execution,
                expected_status=self._expected_assistant_status(execution),
                status="failed",
                error_code="agent_execution_failed",
                display_metadata=display_events,
                assistant_steps=transcript.terminal_snapshot(status="failed"),
                execution_duration_ms=elapsed_duration_ms(),
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
            await release_execution(self.runtime.storage, execution)

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
                text = visible_text(content_to_text(content))
                if text.strip():
                    return text.strip()
        return ""
