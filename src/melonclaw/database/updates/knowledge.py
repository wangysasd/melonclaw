"""已有业务库首次接入知识库。

创建当前八张表和 knowledge_search_chunks 视图，不修改已有业务表。
已有知识库表与当前结构不一致时失败并回滚，不重建、不删表。
"""

from melonclaw.database.knowledge_ddl import create_knowledge


async def upgrade(connection):
    await create_knowledge(connection)
