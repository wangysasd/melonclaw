"""Project、Conversation、历史消息和待处理交互业务。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from melonclaw.core.hitl import (
    aget_pending_interaction,
    approval_batch_id_for,
    serialize_pending_approval,
    serialize_pending_user_question,
)
from melonclaw.core.user_input import USER_INPUT_RECOVERY_REQUIRED
from melonclaw.repository import (
    BusinessRepository,
    ConversationBusyError,
    ConversationMoveError,
    ConversationNotFoundError,
    ProjectNotFoundError,
    UserContext,
)
from melonclaw.services.errors import InvalidUserError
from melonclaw.services.result_index import message_result_refs
from melonclaw.services.runtime import ChatRuntime
from melonclaw.storage.workspace_moves import (
    CopiedWorkspace,
    WorkspaceMoveConflictError,
    copy_conversation_workspace,
)

logger = logging.getLogger(__name__)


class ConversationService:
    """处理用户上下文校验后的 Project/Conversation 业务。"""

    def __init__(self, runtime: ChatRuntime) -> None:
        self.runtime = runtime

    async def resolve_user(self, user_id: str) -> UserContext:
        """从用户的唯一租户归属解析受信任的运行上下文。"""

        storage = self.runtime.storage
        if storage is None:
            raise RuntimeError(self.runtime.startup_error or "数据库仍在启动，请稍候。")
        clean_user_id = user_id.strip()
        if not clean_user_id:
            raise InvalidUserError("user_id 不能为空。")
        context = await storage.get_user_context(clean_user_id)
        if context is None:
            raise InvalidUserError("用户不存在或租户归属未启用。")
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
    ) -> dict[str, Any] | None:
        if conversation["project_id"] is None:
            return None
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
    ) -> dict[str, Any]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
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
    ) -> list[dict[str, Any]]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
        return await storage.list_projects(context.user_id)

    async def update_project(
        self, project_id: UUID, user_id: str,
        *, name: str | None = None, is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
        if name is not None:
            name = " ".join(name.split()).strip()
            if not name or len(name) > 120:
                raise ValueError("项目名称长度须在 1 到 120 个字符之间。")
        if delete:
            # 运行中的会话不能在背后失去所属项目。
            cursor = None
            while True:
                conversations, cursor = await storage.list_conversations(
                    context.user_id, limit=100, cursor=cursor, project_id=project_id,
                )
                for item in conversations:
                    if await storage.get_incomplete_assistant(UUID(item["id"]), context.user_id):
                        raise ConversationBusyError("项目中有进行中的会话，结束后再删除。")
                if cursor is None:
                    break
        return await storage.update_project(
            project_id, context.user_id, name=name, is_pinned=is_pinned, delete=delete,
        )

    async def create_conversation(
        self,
        user_id: str,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
        project = None
        if project_id is not None:
            project = await storage.get_project(project_id, context.user_id)
            if project is None:
                raise ProjectNotFoundError
            self.runtime.project_workspace_dir(project)
        return await storage.create_conversation(
            context.user_id,
            UUID(project["id"]) if project is not None else None,
        )

    async def update_conversation(
        self, conversation_id: UUID, user_id: str,
        *, title: str | None = None, is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
        if title is not None:
            title = " ".join(title.split()).strip()
            if not title or len(title) > 200:
                raise ValueError("会话名称长度须在 1 到 200 个字符之间。")
        if delete and await storage.get_incomplete_assistant(conversation_id, context.user_id):
            raise ConversationBusyError("会话正在运行，结束后再删除。")
        return await storage.update_conversation(
            conversation_id, context.user_id, title=title, is_pinned=is_pinned, delete=delete,
        )

    async def move_conversation_to_project(
        self,
        conversation_id: UUID,
        project_id: UUID,
        user_id: str,
    ) -> dict[str, Any]:
        """普通会话加入项目；成功后只使用原生项目会话的归属与工作区。"""

        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
        lock = await storage.try_advisory_lock(conversation_id)
        if lock is None:
            raise ConversationBusyError("会话正在运行，结束后再移动。")
        copied: CopiedWorkspace | None = None
        committed = False
        try:
            conversation = await storage.get_conversation(conversation_id, context.user_id)
            if conversation is None:
                raise ConversationNotFoundError
            if conversation["project_id"] is not None:
                raise ConversationMoveError("只支持将普通会话移动到项目。", "conversation_already_in_project")
            project = await storage.get_project(project_id, context.user_id)
            if project is None:
                raise ProjectNotFoundError
            if (
                await storage.get_incomplete_assistant(conversation_id, context.user_id)
                or await storage.get_recovery_required_interaction(conversation_id, context.user_id)
            ):
                raise ConversationBusyError("会话仍有未完成的执行或交互，结束后再移动。")
            attachments = await storage.list_conversation_move_attachments(
                conversation_id, context.user_id
            )
            if any(row["status"] == "staged" for row in attachments):
                raise ConversationMoveError("请先发送或移除会话中尚未提交的附件。", "conversation_attachment_staged")
            source = self.runtime.conversation_workspace_dir(conversation_id)
            destination = self.runtime.project_workspace_dir(project)
            for row in attachments:
                if row["status"] in {"staged", "attached"} and not (
                    source / ".attachments" / str(row["id"])
                ).is_dir():
                    raise ConversationMoveError("会话附件文件缺失，无法移动。", "conversation_attachment_missing")
            attachment_versions = tuple(
                (row["id"], row["updated_at"]) for row in attachments
            )
            copy_task = asyncio.create_task(asyncio.to_thread(
                copy_conversation_workspace,
                source,
                destination,
                {row["id"] for row in attachments},
            ))
            try:
                copied = await asyncio.shield(copy_task)
            except asyncio.CancelledError:
                try:
                    copied = await asyncio.shield(copy_task)
                except Exception:  # noqa: BLE001 - 复制失败时已在存储层回滚
                    pass
                raise
            if self.runtime.settings is None:
                raise RuntimeError("运行配置尚未加载。")
            move_task = asyncio.create_task(storage.move_conversation_to_project(
                conversation_id,
                context.user_id,
                project_id,
                attachment_versions=attachment_versions,
                workspace_max_bytes=self.runtime.settings.attachment_project_max_bytes,
            ))
            try:
                moved = await asyncio.shield(move_task)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(move_task)
                except Exception:  # noqa: BLE001 - 事务失败时由 finally 回滚文件
                    pass
                else:
                    committed = True
                raise
            committed = True
            try:
                await asyncio.to_thread(copied.remove_source)
            except (OSError, WorkspaceMoveConflictError):
                logger.warning("会话加入项目成功，但旧工作区清理失败。")
            return moved
        except WorkspaceMoveConflictError as exc:
            raise ConversationMoveError(str(exc), "workspace_move_conflict") from exc
        except IntegrityError as exc:
            raise ConversationMoveError("项目附件上传记录冲突，请重试或选择其他项目。", "project_attachment_conflict") from exc
        except OSError as exc:
            raise ConversationMoveError("工作区文件移动失败，请稍后重试。", "workspace_move_failed", 503) from exc
        finally:
            if copied is not None and not committed:
                try:
                    await asyncio.to_thread(copied.rollback)
                except OSError:
                    logger.warning("会话加入项目失败，本次复制的目标文件未能全部清理。")
            try:
                await storage.release_advisory_lock(lock, conversation_id)
            except Exception:
                if not committed:
                    raise
                logger.warning("会话加入项目成功，但会话锁释放时连接异常。")

    async def list_conversations(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: str | None,
        project_id: UUID | None = None,
        scope: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
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
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id)
        conversation, messages, next_before_seq = await storage.list_messages(
            conversation_id,
            context.user_id,
            limit=limit,
            before_seq=before_seq,
        )
        attachments_by_message, project, incomplete, unresolved = await asyncio.gather(
            storage.list_attachments_for_messages(
                [UUID(message["id"]) for message in messages if message["role"] == "user"]
            ),
            self.project_for_conversation(storage, conversation, context),
            storage.get_incomplete_assistant(conversation_id, context.user_id),
            storage.get_recovery_required_interaction(conversation_id, context.user_id),
        )
        for message in messages:
            message["artifacts"] = message_result_refs(message)
            if message["role"] == "user":
                message["attachments"] = attachments_by_message.get(
                    UUID(message["id"]), []
                )
        # 答案已经收下、但这一轮没能跑完的账本：这一轮只能由用户显式结束，
        # 历史里必须表现为失败，不能再亮出一张"等你回答"的卡片。
        unresolved_assistant_id = (
            str(unresolved["assistant_message_id"]) if unresolved is not None else None
        )
        recovery_assistant = None
        if incomplete is None:
            candidate = await storage.get_latest_assistant(
                conversation_id,
                context.user_id,
            )
            # 业务账本与 Checkpointer 不共享事务：账本保存失败时 assistant
            # 会落到 failed，但 LangGraph Checkpoint 仍可能保留待处理 interrupt。只有在确认仍有
            # pending interaction 后，下面才会把这个候选消息恢复为 interrupted。
            # 已标记 recovery_required 的那一轮例外：它不能再被复活。
            if (
                candidate is not None
                and candidate["status"] == "failed"
                and unresolved_assistant_id != str(candidate["id"])
            ):
                incomplete = candidate
                recovery_assistant = candidate
        pending = None
        if incomplete is not None:
            # 恢复待处理交互必须沿用提问那一轮的模型与客户端能力。
            user_message = await storage.get_request_user_message(
                conversation_id,
                context.user_id,
                incomplete["request_id"],
            )
            agent = await self.runtime.agent_for_conversation(
                conversation,
                project,
                await self.runtime.model_for_message(context.user_id, incomplete),
                self.runtime.capabilities_for_message(user_message),
            )
            pending = await aget_pending_interaction(
                agent,
                self.runtime.conversation_config(conversation_id),
            )
        approvals = [item for item in pending or [] if item.get("kind") == "tool_approval"]
        questions = [item for item in pending or [] if item.get("kind") == "user_question"]
        # recovery_required 的那一轮不再给卡片：用户点提交只会换来"这一轮已经
        # 变了"的报错，卡片本身就是误导。
        if (
            questions
            and incomplete is not None
            and unresolved_assistant_id == str(incomplete["id"])
        ):
            questions = []
        pending_interaction = None
        pending_approval = None
        if approvals:
            if incomplete is None:
                raise RuntimeError("审批请求缺少助手消息记录。")
            approval_batch_id = approval_batch_id_for(
                approvals,
                assistant_message_id=str(incomplete["id"]),
            )
            pending_approval = serialize_pending_approval(
                approvals,
                approval_batch_id=approval_batch_id,
                assistant_message_id=str(incomplete["id"]),
            )
            if recovery_assistant is not None:
                display_metadata = dict(recovery_assistant["display_metadata"])
                display_metadata["pending_approval"] = {
                    "approval_batch_id": approval_batch_id,
                    "assistant_message_id": str(incomplete["id"]),
                }
                await storage.update_assistant(
                    conversation_id,
                    UUID(recovery_assistant["id"]),
                    status="interrupted",
                    display_metadata=display_metadata,
                    error_code=None,
                    expected_status="failed",
                )
        if len(questions) == 1 and len(pending or []) == 1:
            if incomplete is None:
                raise RuntimeError("用户问题缺少助手消息记录。")
            settings = self.runtime.settings
            if settings is None:
                raise RuntimeError("运行配置尚未加载。")
            interaction = await storage.create_or_get_user_interaction(
                conversation_id,
                UUID(incomplete["id"]),
                context.user_id,
                str(questions[0]["id"]),
                questions[0],
                settings.user_input_ttl_seconds,
            )
            pending_interaction = serialize_pending_user_question(
                questions[0],
                interaction_id=interaction["id"],
                assistant_message_id=interaction["assistant_message_id"],
                expires_at=interaction["expires_at"],
            )
            if recovery_assistant is not None:
                display_metadata = dict(recovery_assistant["display_metadata"])
                display_metadata["pending_interaction"] = pending_interaction
                await storage.update_assistant(
                    conversation_id,
                    UUID(recovery_assistant["id"]),
                    status="interrupted",
                    display_metadata=display_metadata,
                    error_code=None,
                    expected_status="failed",
                )
        if unresolved_assistant_id is not None:
            _mark_recovery_required_failure(messages, unresolved_assistant_id)
        return {
            "conversation": conversation,
            "items": messages,
            "next_before_seq": next_before_seq,
            "pending_approval": pending_approval,
            "pending_interaction": pending_interaction,
        }


def _mark_recovery_required_failure(
    messages: list[dict[str, Any]],
    assistant_message_id: str,
) -> None:
    """把"答案已收但没跑完"的那一轮在历史里标成明确失败。

    只改这一份返回给浏览器的副本：数据库里的助手状态由 Mark 流程负责，
    历史查询不应该偷偷写库。这样用户在界面上看到的是"这一轮没能继续"，
    而不是一张永远在等他、点了还报错的问题卡片。
    """

    for message in messages:
        if str(message.get("id")) != assistant_message_id:
            continue
        if message.get("role") != "assistant":
            continue
        message["status"] = "failed"
        message["error_code"] = USER_INPUT_RECOVERY_REQUIRED
        return
