"""首次接入任务中心：创建当前完整三表并校验；不迁移旧开发结构。"""

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect

from melonclaw.database.schema import metadata

TASK_TABLE_NAMES = (
    "tasks",
    "task_runs",
    "task_operations",
)


async def upgrade(connection):
    def apply(sync):
        metadata.create_all(sync, tables=[metadata.tables[name] for name in TASK_TABLE_NAMES])
        inspector = inspect(sync)
        for name in TASK_TABLE_NAMES:
            table = metadata.tables[name]
            actual = {column["name"] for column in inspector.get_columns(name)}
            if actual != set(table.columns.keys()):
                raise RuntimeError(f"{name} 结构不匹配；任务中心升级未提交。")
            indexes = {item["name"] for item in inspector.get_indexes(name)}
            if not {index.name for index in table.indexes} <= indexes:
                raise RuntimeError(f"{name} 缺少索引；任务中心升级未提交。")
            uniques = {
                tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)
            }
            expected_uniques = {
                tuple(c.columns.keys())
                for c in table.constraints
                if isinstance(c, UniqueConstraint)
            }
            foreign_keys = {
                (
                    tuple(item["constrained_columns"]),
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                )
                for item in inspector.get_foreign_keys(name)
            }
            expected_foreign_keys = {
                (
                    tuple(c.columns.keys()),
                    c.referred_table.name,
                    tuple(e.column.name for e in c.elements),
                )
                for c in table.constraints
                if isinstance(c, ForeignKeyConstraint)
            }
            if not expected_uniques <= uniques or not expected_foreign_keys <= foreign_keys:
                raise RuntimeError(f"{name} 缺少唯一键或外键；任务中心升级未提交。")
            checks = {item["name"] for item in inspector.get_check_constraints(name)}
            expected = {
                c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
            }
            if not expected <= checks:
                raise RuntimeError(f"{name} 缺少约束；任务中心升级未提交。")

    await connection.run_sync(apply)
