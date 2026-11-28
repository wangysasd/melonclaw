"""扩展知识库事件类型，使停用和恢复可以同事务入账。

只替换 knowledge_events.action 的检查约束。不改已有事件，不清理文档、版本或原件。
"""

from sqlalchemy import text

from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.database.knowledge_schema import event_action_check_sql


async def upgrade(connection):
    present = await connection.scalar(text("SELECT to_regclass('knowledge_events')"))
    if present is None:
        raise DatabaseSchemaError("缺少 knowledge_events；请先完成知识库表升级。")
    expression = event_action_check_sql()
    await connection.execute(text(
        "ALTER TABLE knowledge_events DROP CONSTRAINT IF EXISTS ck_kb_event_action"
    ))
    await connection.execute(text(
        "ALTER TABLE knowledge_events ADD CONSTRAINT ck_kb_event_action CHECK (" + expression + ")"
    ))
