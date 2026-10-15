"""任务与会话消息的事务内交付；不运行聊天 Agent。"""

from sqlalchemy import func, insert, select, update

from melonclaw.core.task_errors import TaskError
from melonclaw.core.task_messages import assistant_values, task_message_rows, task_request_id
from melonclaw.database.schema import chat_conversations, chat_messages, tasks


async def append_run(connection, task, run):
    conversation = await connection.scalar(
        select(chat_conversations.c.id)
        .where(
            chat_conversations.c.id == task["conversation_id"],
            chat_conversations.c.user_id == task["owner_user_id"],
            chat_conversations.c.status == "active",
        )
        .with_for_update()
    )
    if conversation is None:
        raise TaskError("conversation_unavailable", "任务会话不可用，请检查任务状态。")
    seq = await connection.scalar(
        select(func.coalesce(func.max(chat_messages.c.seq), 0)).where(
            chat_messages.c.conversation_id == conversation
        )
    )
    await connection.execute(insert(chat_messages), task_message_rows(task, run, int(seq) + 1))
    await connection.execute(
        update(chat_conversations)
        .where(chat_conversations.c.id == conversation)
        .values(updated_at=run["created_at"])
    )


async def sync_run(connection, run):
    conversation_id = await connection.scalar(
        select(tasks.c.conversation_id).where(
            tasks.c.id == run["task_id"],
            tasks.c.owner_user_id == run["owner_user_id"],
            tasks.c.tenant_id == run["tenant_id"],
        )
    )
    # 与普通聊天消息追加使用同一会话行锁，避免序号/更新时间竞争。
    await connection.execute(
        select(chat_conversations.c.id)
        .where(chat_conversations.c.id == conversation_id)
        .with_for_update()
    )
    values = assistant_values(run)
    result = await connection.execute(
        update(chat_messages)
        .where(
            chat_messages.c.conversation_id == conversation_id,
            chat_messages.c.request_id == task_request_id(run),
            chat_messages.c.role == "assistant",
        )
        .values(**values)
        .returning(chat_messages.c.id)
    )
    if result.first() is None:
        raise RuntimeError("任务执行缺少对应的会话消息，事务未提交。")
    await connection.execute(
        update(chat_conversations)
        .where(chat_conversations.c.id == conversation_id)
        .values(updated_at=func.greatest(chat_conversations.c.updated_at, values["updated_at"]))
    )
