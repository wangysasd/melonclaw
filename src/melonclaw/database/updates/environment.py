"""保留业务历史，增加环境变量资源和受控审批暂存。"""

from melonclaw.database.schema import environment_operations, environment_variables


async def upgrade(connection):
    for table in (environment_variables, environment_operations):
        await connection.run_sync(lambda sync, table=table: table.create(sync, checkfirst=True))
