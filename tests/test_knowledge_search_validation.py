"""Repository 参数边界测试，不需要数据库连接。"""

import asyncio
from uuid import uuid4

import pytest

from melonclaw.core.knowledge import KnowledgeError
from melonclaw.repository.knowledge_search import KnowledgeSearchRepositoryMixin


def test_open_text_rejects_postgres_integer_overflow_before_database_access():
    repo = object.__new__(KnowledgeSearchRepositoryMixin)

    async def run():
        with pytest.raises(KnowledgeError, match="正文偏移无效"):
            await repo.knowledge_open_text("owner", uuid4(), uuid4(), offset=2_147_483_648, limit=1)

    asyncio.run(run())
