"""固定任务会话的累计历史与增量消息读取。"""


from sqlalchemy import or_, select

from melonclaw.core.task_errors import task_not_found
from melonclaw.core.task_history import history_messages
from melonclaw.database.schema import chat_conversations, chat_messages, tasks
from melonclaw.repository.mappers import _message_dict
from melonclaw.repository.task_history import load_history
from melonclaw.repository.tasks import scope


async def bind_context(conn, user_id, context):
    """消息事务内复核累计历史来源，与消息对一起提交。"""
    if "task_history" in context:
        await load_history(conn, user_id, context["task_history"])


class TaskChatRepository:
    def __init__(self, engine):
        self.engine = engine

    async def updates(self, user_id, task_id, after_seq, pending_ids):
        async with self.engine.connect() as conn:
            conversation_id = await conn.scalar(select(tasks.c.conversation_id).join(
                chat_conversations, chat_conversations.c.id == tasks.c.conversation_id
            ).where(tasks.c.id == task_id, *scope(tasks, user_id), chat_conversations.c.status == "active"))
            if conversation_id is None:
                raise task_not_found()
            rows = (await conn.execute(select(chat_messages).where(
                chat_messages.c.conversation_id == conversation_id,
                or_(chat_messages.c.seq > after_seq, chat_messages.c.id.in_(pending_ids)),
            ).order_by(chat_messages.c.seq).limit(100))).mappings().all()
            return {"conversation_id": str(conversation_id), "items": [_message_dict(row) for row in rows]}

    async def message_context(self, user_id, conversation_id, message_id):
        async with self.engine.connect() as conn:
            metadata = (
                await conn.execute(
                    select(chat_messages.c.display_metadata)
                    .join(
                        chat_conversations,
                        chat_messages.c.conversation_id == chat_conversations.c.id,
                    )
                    .where(
                        chat_messages.c.id == message_id,
                        chat_messages.c.role == "user",
                        chat_conversations.c.id == conversation_id,
                        chat_conversations.c.user_id == user_id,
                        chat_conversations.c.status == "active",
                    )
                )
            ).scalar_one_or_none()
            snapshot = (metadata or {}).get("task_history")
            value = {}
            if snapshot:
                rows = await load_history(conn, user_id, snapshot)
                value.update(history_messages=history_messages(rows, snapshot), completed_runs=snapshot["completed_runs"])
            return value


    async def bound_history(self, user_id, conversation_id, message_id):
        async with self.engine.connect() as conn:
            metadata = await conn.scalar(select(chat_messages.c.display_metadata).join(chat_conversations).where(
                chat_messages.c.id == message_id, chat_messages.c.conversation_id == conversation_id,
                chat_messages.c.role == "user", chat_conversations.c.user_id == user_id,
                chat_conversations.c.status == "active",
            ))
            return (metadata or {}).get("task_history")
