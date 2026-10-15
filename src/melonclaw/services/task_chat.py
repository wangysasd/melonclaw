"""任务会话累计历史、原文读取和后台消息同步。"""

import asyncio
from uuid import UUID

from melonclaw.core.task_errors import TaskError
from melonclaw.repository.task_chat import TaskChatRepository
from melonclaw.repository.task_history import capture_history, load_history
from melonclaw.services.task_history import prepare_history, read_snapshot


class TaskChatService:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def repository(self):
        return TaskChatRepository(self.runtime.require_ready().engine)

    async def hydrate(self, context, message_id):
        storage = self.runtime.require_ready()
        user = await storage.get_user_context(context.user_id)
        if user is None or user.tenant_id != context.tenant_id:
            raise TaskError("identity_changed", "当前身份已失效。", 403)
        return await self.repository.message_context(
            context.user_id, UUID(context.conversation_id), UUID(message_id)
        )

    async def history_selection(self, user_id, conversation_id, model, content):
        try:
            async with asyncio.timeout(self.runtime.settings.task_history_timeout_seconds):
                async with self.runtime.require_ready().engine.connect() as conn:
                    snapshot = await capture_history(conn, user_id, conversation_id)
                    rows = await load_history(conn, user_id, snapshot)
                snapshot = await prepare_history(snapshot, rows, model, self.runtime.settings, content=content)
        except TimeoutError as exc:
            raise TaskError("history_summary_timeout", "任务历史准备超过时限，请稍后重试。") from exc
        return {"task_history": snapshot}

    async def read_history(self, context, **kwargs):
        storage = self.runtime.require_ready()
        user = await storage.get_user_context(context.user_id)
        if user is None or user.tenant_id != context.tenant_id:
            raise TaskError("identity_changed", "当前身份已失效。", 403)
        value = await self.repository.bound_history(context.user_id, UUID(context.conversation_id), UUID(context.user_message_id))
        if value is None:
            raise TaskError("history_unavailable", "本轮没有绑定任务历史。")
        return await read_snapshot(storage.engine, context.user_id, value, **kwargs)

    async def updates(self, user_id, task_id, after_seq, pending_ids):
        from melonclaw.services.result_index import message_result_refs

        value = await self.repository.updates(user_id, task_id, after_seq, pending_ids)
        attachments = await self.runtime.require_ready().list_attachments_for_messages(
            [UUID(row["id"]) for row in value["items"] if row["role"] == "user"]
        )
        for row in value["items"]:
            row["artifacts"] = message_result_refs(row)
            if row["role"] == "user":
                row["attachments"] = attachments.get(UUID(row["id"]), [])
        return value

