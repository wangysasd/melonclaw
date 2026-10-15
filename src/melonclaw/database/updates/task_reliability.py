"""删除无消费者的任务通知结构，允许过期回执退化为永久拒重标记。"""

from sqlalchemy import inspect, text


async def upgrade(connection):
    await connection.execute(text("DROP INDEX IF EXISTS ix_task_runs_notifications"))
    await connection.execute(text("ALTER TABLE task_runs DROP COLUMN IF EXISTS notification_read_at"))
    await connection.execute(text("ALTER TABLE task_operations ALTER COLUMN response DROP NOT NULL"))
    await connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_task_operations_retention "
        "ON task_operations (created_at) WHERE response IS NOT NULL"
    ))

    def verify(sync):
        inspector = inspect(sync)
        if "notification_read_at" in {c["name"] for c in inspector.get_columns("task_runs")}:
            raise RuntimeError("任务通知字段移除失败，升级未提交。")
        response = next(c for c in inspector.get_columns("task_operations") if c["name"] == "response")
        if not response["nullable"]:
            raise RuntimeError("任务操作回执仍不可为空，升级未提交。")

    await connection.run_sync(verify)
