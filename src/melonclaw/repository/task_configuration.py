"""当前聊天配置任务的来源、会话与并发校验。"""

from sqlalchemy import select

from melonclaw.core.task_errors import TaskError, task_not_found
from melonclaw.database.schema import chat_conversations, chat_messages, tasks


async def conversation_task(conn, user, conversation_id, message_id, request_id, *,
                            require_pending=False):
    conversation = (await conn.execute(select(chat_conversations).where(
        chat_conversations.c.id == conversation_id,
        chat_conversations.c.user_id == user.user_id,
        chat_conversations.c.status == "active",
    ).with_for_update())).mappings().first()
    if conversation is None:
        raise task_not_found()
    if conversation["project_id"] is not None:
        raise TaskError("project_task_forbidden", "请在普通会话中配置任务，任务不能绑定项目。", 422)
    source = (await conn.execute(select(chat_messages).where(
        chat_messages.c.id == message_id, chat_messages.c.conversation_id == conversation_id,
        chat_messages.c.request_id == request_id, chat_messages.c.role == "user",
        chat_messages.c.status == "completed",
    ))).mappings().first()
    if source is None or any(key in source["display_metadata"] for key in ("task_run", "task_setup")):
        raise TaskError("invalid_source", "任务配置需要本轮真实的用户消息。", 403)
    if require_pending:
        pending = await conn.scalar(select(chat_messages.c.id).where(
            chat_messages.c.conversation_id == conversation_id,
            chat_messages.c.request_id == request_id, chat_messages.c.role == "assistant",
            chat_messages.c.status == "pending",
        ))
        if pending is None:
            raise TaskError("request_finished", "本轮聊天已结束，请重新发送配置要求。")
    current = (await conn.execute(select(tasks).where(
        tasks.c.conversation_id == conversation_id,
    ).with_for_update())).mappings().first()
    if current and (current["owner_user_id"] != user.user_id or current["tenant_id"] != user.tenant_id):
        raise task_not_found()
    return dict(current) if current else None
