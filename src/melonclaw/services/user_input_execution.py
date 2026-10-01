"""用户问题答案的校验、幂等接收与 Agent 恢复。

取消（用户主动跳过，或问题过期后由服务端代答）同样走这条恢复通道：
只有真正用 ``Command(resume=...)`` 唤醒 Agent，Checkpoint 里挂起的
interrupt 才会解除，会话才能继续接收新消息。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from melonclaw.core.hitl import (
    aget_pending_interaction,
    build_user_input_resume_command,
)
from melonclaw.repository import (
    ConversationBusyError,
    ConversationNotFoundError,
    UserInteractionExpiredError,
)
from melonclaw.repository.user_interaction_lifecycle import (
    ANSWERED_STATUSES,
    UNANSWERED_STATUSES,
    UserInteractionStatus,
)
from melonclaw.services.execution import ExecutionService, PreparedExecution
from melonclaw.tool.user_input import (
    USER_INPUT_ANSWER_CANCELLED,
    USER_INPUT_KIND,
)

CANCEL_ANSWER: dict[str, Any] = {"type": USER_INPUT_ANSWER_CANCELLED}


def _is_expired(expires_at: str) -> bool:
    """判断问题账本是否已经过期；非法数据直接失败。"""

    parsed = datetime.fromisoformat(expires_at)
    if parsed.tzinfo is None:
        raise ValueError("用户问题的 expires_at 必须包含时区。")
    return parsed <= datetime.now(UTC)


class CancelReason(StrEnum):
    """发新消息前需要代答取消的原因。

    两种原因共用同一条恢复通道，差别只在「怎么定位账本」和「取消前要不要
    改写账本与助手状态」，这些差别全部写在 :data:`_CANCEL_POLICY` 里，
    不再用布尔参数在函数体里分叉。

    值直接引用账本状态常量：取消原因本来就是账本状态的一部分，字面量
    不该在这里再写一遍。
    """

    EXPIRED = UserInteractionStatus.EXPIRED
    RECOVERY_REQUIRED = UserInteractionStatus.RECOVERY_REQUIRED


@dataclass(frozen=True)
class _CancelPolicy:
    """一种取消原因对应的完整收尾方式。"""

    # 定位提问那一轮的助手消息：取未完成的那一条，还是取最新一条。
    assistant: Literal["incomplete", "latest"]
    # 是否要求账本已经过期。未过期的 waiting 卡片不能被新消息误取消。
    require_expired: bool
    # 取消前是否要把旧账本换成一本新的。recovery 那轮的原账本已经判定失败，
    # 必须换一本新账本才能重新进入接收流程。
    reopen_ledger: bool


_CANCEL_POLICY: dict[CancelReason, _CancelPolicy] = {
    CancelReason.EXPIRED: _CancelPolicy(
        assistant="incomplete",
        require_expired=True,
        reopen_ledger=False,
    ),
    CancelReason.RECOVERY_REQUIRED: _CancelPolicy(
        assistant="latest",
        require_expired=False,
        reopen_ledger=True,
    ),
}


def _cancel_reason(candidate: dict[str, Any] | None) -> CancelReason | None:
    """把一次账本查询的结果翻译成取消原因；不需要收尾时返回 None。"""

    if candidate is None:
        return None
    status = candidate["status"]
    if status == UserInteractionStatus.RECOVERY_REQUIRED:
        return CancelReason.RECOVERY_REQUIRED
    if status in UNANSWERED_STATUSES:
        return CancelReason.EXPIRED
    return None


class UserInputExecutionService:
    """把浏览器答案安全地转换成当前 checkpoint 的恢复命令。"""

    def __init__(self, execution: ExecutionService) -> None:
        self.execution = execution

    async def prepare(
        self,
        conversation_id: UUID,
        user_id: str,
        interaction_id: UUID,
        assistant_message_id: UUID,
        decision_request_id: str,
        answer: Any,
        *,
        allow_expired: bool = False,
    ) -> tuple[PreparedExecution, Any, dict[str, Any]] | dict[str, Any]:
        runtime = self.execution.runtime
        conversations = self.execution.conversations
        storage = runtime.require_ready()
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError
        context = await conversations.resolve_user(user_id)
        stored_interaction = await storage.get_user_interaction(
            conversation_id,
            context.user_id,
            interaction_id,
        )
        if stored_interaction is not None:
            if stored_interaction["assistant_message_id"] != str(assistant_message_id):
                raise ValueError("用户问题不存在或已刷新，请重新打开会话。")
            if stored_interaction["status"] in ANSWERED_STATUSES:
                stored_question = dict(stored_interaction["payload"])
                stored_question["id"] = stored_interaction["interrupt_id"]
                _, normalized_answer = build_user_input_resume_command(
                    stored_question,
                    answer,
                )
                accepted, is_new = await storage.accept_user_interaction(
                    conversation_id,
                    context.user_id,
                    interaction_id,
                    decision_request_id,
                    normalized_answer,
                )
                if not is_new:
                    return {
                        "interaction_id": accepted["id"],
                        "decision_request_id": accepted["decision_request_id"],
                        "assistant_message_id": accepted["assistant_message_id"],
                    }
            elif (
                stored_interaction["status"] == UserInteractionStatus.EXPIRED
                and not allow_expired
            ):
                raise UserInteractionExpiredError
        project = await conversations.project_for_conversation(
            storage,
            conversation,
            context,
        )
        assistant = await storage.get_incomplete_assistant(
            conversation_id,
            context.user_id,
        )
        if assistant is None or assistant["id"] != str(assistant_message_id):
            raise ValueError("找不到等待用户回答的业务消息记录。")
        request_record = await storage.find_request(
            conversation_id,
            context.user_id,
            assistant["request_id"],
        )
        model = await runtime.model_for_message(context.user_id, assistant)
        agent = await runtime.agent_for_conversation(
            conversation,
            project,
            model,
            runtime.capabilities_for_message(
                request_record.user_message if request_record is not None else None
            ),
        )
        # 先抢锁，再校验：设计要求的顺序是「取锁 → 锁内重读 assistant /  pending
        # → 校验 → 原子接收」。反过来做的话，两个标签页并发提交时各自都能用自己
        # 那份旧快照通过问题校验，失败方最后只收到一句「已提交过不同的答案」，
        # 而真实原因是上下文已经变了；失败方还会在拿到锁之前先写一次库。
        lock_connection = await storage.try_advisory_lock(conversation_id)
        if lock_connection is None:
            raise ConversationBusyError("当前会话正在处理另一条消息，请稍候。")
        try:
            latest_assistant = await storage.get_incomplete_assistant(
                conversation_id,
                context.user_id,
            )
            if (
                latest_assistant is None
                or latest_assistant["id"] != str(assistant_message_id)
            ):
                raise ValueError("找不到等待用户回答的业务消息记录。")
            pending = await aget_pending_interaction(
                agent,
                runtime.conversation_config(conversation_id),
            )
            questions = [
                item for item in pending or [] if item.get("kind") == "user_question"
            ]
            if len(questions) != 1 or len(pending or []) != 1:
                raise ValueError("当前没有等待处理的用户问题卡片。")
            question = questions[0]
            settings = runtime.settings
            if settings is None:
                raise RuntimeError("运行配置尚未加载。")
            if (
                stored_interaction is not None
                and stored_interaction["status"] in UNANSWERED_STATUSES
                and stored_interaction["interrupt_id"] == str(question["id"])
                and stored_interaction["payload"] == question
            ):
                # 过期账本必须原地接收服务端取消，不能先归档再创建一张拥有新 TTL
                # 的卡片；否则一次历史刷新就会把已经过期的问题重新续期。
                interaction = stored_interaction
            else:
                interaction = await storage.create_or_get_user_interaction(
                    conversation_id,
                    UUID(assistant["id"]),
                    context.user_id,
                    str(question["id"]),
                    question,
                    settings.user_input_ttl_seconds,
                )
            if interaction["id"] != str(interaction_id):
                raise ValueError("用户问题不存在或已刷新，请重新打开会话。")
            command, normalized_answer = build_user_input_resume_command(
                question,
                answer,
            )
            accepted, is_new = await storage.accept_user_interaction(
                conversation_id,
                context.user_id,
                interaction_id,
                decision_request_id,
                normalized_answer,
                allow_expired=allow_expired,
            )
            if not is_new:
                await storage.release_advisory_lock(lock_connection, conversation_id)
                return {
                    "interaction_id": accepted["id"],
                    "decision_request_id": accepted["decision_request_id"],
                    "assistant_message_id": accepted["assistant_message_id"],
                }
            return (
                PreparedExecution(
                    conversation_id=conversation_id,
                    project_id=UUID(project["id"]) if project is not None else None,
                    project_name=project["name"] if project is not None else None,
                    workdir_path=str(runtime.workspace_dir(conversation, project)),
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    tenant_name=context.tenant_name_zh,
                    tenant_role=context.tenant_role,
                    tenant_status=context.tenant_status,
                    request_id=assistant["request_id"],
                    run_id=str(uuid4()),
                    worker_id=runtime.worker_id,
                    config=runtime.conversation_config(conversation_id),
                    assistant_message_id=UUID(assistant["id"]),
                    agent=agent,
                    model=model,
                    lock_connection=lock_connection,
                    content=assistant["content"],
                    user_interaction_id=interaction_id,
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
                accepted,
            )
        except Exception:
            await storage.release_advisory_lock(lock_connection, conversation_id)
            raise

    async def cancel_before_new_message(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> bool:
        """发新消息前一次判断并收尾挂起的提问，正常 waiting 卡片保持不动。

        账本只查一次，查询结果直接交给 :meth:`cancel`：判断要不要取消和真正
        取消必须基于同一份数据，否则中间几次数据库往返之后可能已经变了。
        """

        clean_user_id = user_id.strip()
        if not clean_user_id:
            return False
        storage = self.execution.runtime.require_ready()
        candidate = await storage.get_user_interaction_cancellation_candidate(
            conversation_id,
            clean_user_id,
        )
        reason = _cancel_reason(candidate)
        if candidate is None or reason is None:
            return False
        return await self.cancel(conversation_id, user_id, reason, candidate)

    async def cancel(
        self,
        conversation_id: UUID,
        user_id: str,
        reason: CancelReason,
        candidate: dict[str, Any],
    ) -> bool:
        """按 reason 声明的策略做一次代答取消，解锁仍挂起的 Checkpoint。

        返回是否真的执行了一次恢复。账本已不存在、尚未过期，或 Checkpoint
        已经不再挂起时返回 False。

        ``recovery_required`` 唯一的出口也在这里：自动重放会把可能带副作用的
        那一段再跑一遍，所以必须等一个明确的用户动作。用户愿意发新消息，就
        等于放弃了上一轮的结果，此时用一次取消把 Checkpoint 唤醒让它收尾，
        新消息才能正常开跑。
        """

        prepared = await self._prepare_cancel(
            conversation_id,
            user_id,
            reason,
            candidate,
        )
        if prepared is None:
            return False
        return await self._run_cancel(prepared)

    async def _prepare_cancel(
        self,
        conversation_id: UUID,
        user_id: str,
        reason: CancelReason,
        candidate: dict[str, Any],
    ) -> tuple[PreparedExecution, Any, dict[str, Any]] | dict[str, Any] | None:
        policy = _CANCEL_POLICY[reason]
        runtime = self.execution.runtime
        conversations = self.execution.conversations
        storage = runtime.require_ready()
        conversation = await storage.get_conversation(conversation_id, user_id)
        if conversation is None:
            return None
        context = await conversations.resolve_user(user_id)
        if policy.assistant == "latest":
            assistant = await storage.get_latest_assistant(
                conversation_id,
                context.user_id,
            )
        else:
            assistant = await storage.get_incomplete_assistant(
                conversation_id,
                context.user_id,
            )
        if (
            assistant is None
            or candidate is None
            or str(assistant["id"]) != str(candidate["assistant_message_id"])
        ):
            return None
        project = await conversations.project_for_conversation(
            storage,
            conversation,
            context,
        )
        # 代答取消也必须沿用提问那一轮声明的能力，否则恢复用的 Agent 可能
        # 没有 ask_user，工具集和提问时不一致。
        request_record = await storage.find_request(
            conversation_id,
            context.user_id,
            assistant["request_id"],
        )
        agent = await runtime.agent_for_conversation(
            conversation,
            project,
            await runtime.model_for_message(context.user_id, assistant),
            runtime.capabilities_for_message(
                request_record.user_message if request_record is not None else None
            ),
        )
        pending = await aget_pending_interaction(
            agent,
            runtime.conversation_config(conversation_id),
        )
        questions = [
            item for item in pending or [] if item.get("kind") == USER_INPUT_KIND
        ]
        if len(questions) != 1 or len(pending or []) != 1:
            return None
        question = questions[0]
        settings = runtime.settings
        if settings is None:
            raise RuntimeError("运行配置尚未加载。")
        if policy.require_expired and not _is_expired(candidate["expires_at"]):
            return None
        if not policy.reopen_ledger and (
            candidate["interrupt_id"] != str(question["id"])
            or candidate["payload"] != question
        ):
            # recovery 那轮的账本是下面新建的，没有可比对的旧快照；其余情况必须
            # 确认账本和 checkpoint 还是同一个问题，否则唤醒的可能不是当初提问
            # 的那一轮。
            return None
        interaction = candidate
        if policy.reopen_ledger:
            interaction = await storage.create_or_get_user_interaction(
                conversation_id,
                UUID(assistant["id"]),
                context.user_id,
                str(question["id"]),
                question,
                settings.user_input_ttl_seconds,
            )
            await storage.discard_recovery_required_interaction(
                conversation_id,
                UUID(candidate["id"]),
            )
            # 恢复执行的 CAS 条件只接受 interrupted。这里把已经明确失败的原轮次
            # 恢复成可收尾状态；若后续准备失败，历史查询仍能从 checkpoint 重建卡片。
            await storage.update_assistant(
                conversation_id,
                UUID(assistant["id"]),
                status="interrupted",
                display_metadata=dict(assistant["display_metadata"]),
                error_code=None,
                expected_status=("failed", "cancelled"),
            )
        return await self.prepare(
            conversation_id,
            context.user_id,
            UUID(interaction["id"]),
            UUID(assistant["id"]),
            str(uuid4()),
            dict(CANCEL_ANSWER),
            allow_expired=True,
        )

    async def _run_cancel(
        self,
        prepared: tuple[PreparedExecution, Any, dict[str, Any]] | dict[str, Any],
    ) -> bool:
        """消费一次取消恢复的完整事件流，确保会话锁最终被释放。"""

        if isinstance(prepared, dict):
            # 同样的取消已经接收过，直接重放，不必再次唤醒 Agent。
            return True
        execution, command, _ = prepared
        async for _ in self.execution.stream_execution(
            execution,
            agent_input=command,
        ):
            pass
        return True


__all__ = ["CancelReason", "UserInputExecutionService"]
