"""固定任务会话历史快照；身份、来源和原文均由数据库验证。"""

from uuid import UUID

from sqlalchemy import select

from melonclaw.core.task_errors import TaskError, task_not_found
from melonclaw.core.task_history import source_digest
from melonclaw.database.schema import chat_conversations, chat_messages, task_runs, tasks
from melonclaw.repository.tasks import scope


async def visible_task(conn, user_id, conversation_id):
    row = (await conn.execute(select(tasks).join(chat_conversations, tasks.c.conversation_id == chat_conversations.c.id).where(
        tasks.c.conversation_id == conversation_id, *scope(tasks, user_id),
        chat_conversations.c.user_id == user_id, chat_conversations.c.status == "active",
    ))).mappings().first()
    if row is None:
        raise task_not_found()
    return row


async def capture_history(conn, user_id, conversation_id):
    task = await visible_task(conn, user_id, conversation_id)
    rows = (await conn.execute(select(chat_messages).where(
        chat_messages.c.conversation_id == conversation_id,
        chat_messages.c.status.in_(["completed", "failed", "cancelled"]),
    ).order_by(chat_messages.c.seq))).mappings().all()
    sources = [{"id": str(row["id"]), "seq": row["seq"], "sha256": source_digest(row)} for row in rows]
    candidates = [row["display_metadata"]["task_history"] for row in reversed(rows) if row["display_metadata"].get("task_history", {}).get("summary")]
    prior = await conn.scalar(select(task_runs.c.config_snapshot).where(
        task_runs.c.task_id == task["id"], task_runs.c.status.in_(["succeeded", "failed", "cancelled"]),
        task_runs.c.config_snapshot["history"]["summary"].astext != "",
    ).order_by(task_runs.c.finished_at.desc()).limit(1))
    if prior is not None:
        candidates.insert(0, prior["history"])
    summary, summary_until = "", 0
    for cached in candidates:
        until = cached["summary_until"]
        if [source for source in cached["sources"] if source["seq"] <= until] == [source for source in sources if source["seq"] <= until]:
            summary, summary_until = cached["summary"], until
            break
    return {"task_id": str(task["id"]), "conversation_id": str(conversation_id), "sources": sources,
            "completed_runs": sum(1 for row in rows if row["role"] == "assistant" and row["display_metadata"].get("task_run", {}).get("status") == "succeeded"),
            "summary": summary, "summary_until": summary_until}


async def load_history(conn, user_id, snapshot):
    task = await visible_task(conn, user_id, UUID(snapshot["conversation_id"]))
    if str(task["id"]) != snapshot["task_id"]:
        raise task_not_found()
    sources = snapshot["sources"]
    rows = (await conn.execute(select(chat_messages).where(
        chat_messages.c.conversation_id == task["conversation_id"],
        chat_messages.c.id.in_([UUID(source["id"]) for source in sources]),
    ).order_by(chat_messages.c.seq))).mappings().all()
    if len(rows) != len(sources) or any(str(row["id"]) != source["id"] or source_digest(row) != source["sha256"] for row, source in zip(rows, sources, strict=True)):
        raise TaskError("history_changed", "任务历史来源已变化，请重新发送消息。")
    return rows
