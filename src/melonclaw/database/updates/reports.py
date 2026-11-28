"""为已有库增加投诉举报表，不改动既有业务数据。"""

from sqlalchemy.ext.asyncio import AsyncConnection

from melonclaw.database.schema import report_events, reports


async def upgrade(connection: AsyncConnection) -> None:
    await connection.run_sync(lambda sync: reports.create(sync, checkfirst=True))
    await connection.run_sync(lambda sync: report_events.create(sync, checkfirst=True))
