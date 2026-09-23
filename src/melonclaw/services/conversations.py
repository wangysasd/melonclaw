"""Project、Conversation、历史消息和待处理交互业务。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

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

    async def update_project(
        self, project_id: UUID, user_id: str, tenant_id: str | None = None,
        *, name: str | None = None, is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
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
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
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
        self, conversation_id: UUID, user_id: str, tenant_id: str | None = None,
        *, title: str | None = None, is_pinned: bool | None = None, delete: bool = False,
    ) -> dict[str, Any] | None:
        storage = self.runtime.require_ready()
        context = await self.resolve_user(user_id, tenant_id)
        if title is not None:
            title = " ".join(title.split()).strip()
            if not title or len(title) > 200:
                raise ValueError("会话名称长度须在 1 到 200 个字符之间。")
        if delete and await storage.get_incomplete_assistant(conversation_id, context.user_id):
            raise ConversationBusyError("会话正在运行，结束后再删除。")
        return await storage.update_conversation(
            conversation_id, context.user_id, title=title, is_pinned=is_pinned, delete=delete,
        )

    async def list_conversations(
        self,
        user_id: str,
        *,
        tenant_id: str | None = None,
        limit: int,
        cursor: str | None,
        project_id: UUID | None = None,
        scope: str | None = None,
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
            scope=scope,
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
        attachments_by_message = await storage.list_attachments_for_messages(
            [UUID(message["id"]) for message in messages if message["role"] == "user"]
        )
        for message in messages:
            if message["role"] == "user":
                message["attachments"] = attachments_by_message.get(
                    UUID(message["id"]), []
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
        # 答案已经收下、但这一轮没能跑完的账本：这一轮只能由用户显式结束，
        # 历史里必须表现为失败，不能再亮出一张"等你回答"的卡片。
        unresolved = await storage.get_recovery_required_interaction(
            conversation_id,
            context.user_id,
        )
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
        model = (
            self.runtime.model_for_message(incomplete)
            if incomplete is not None
            else self.runtime.resolve_model()
        )
        # 恢复待处理交互必须沿用提问那一轮声明的能力，否则拿到的 Agent 可能
        # 没有 ask_user，读出来的 pending 与浏览器看到的卡片对不上。
        capabilities: tuple[str, ...] = ()
        if incomplete is not None:
            request_record = await storage.find_request(
                conversation_id,
                context.user_id,
                incomplete["request_id"],
            )
            capabilities = self.runtime.capabilities_for_message(
                request_record.user_message if request_record is not None else None
            )
        agent = await self.runtime.agent_for_conversation(
            conversation, project, model, capabilities
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
