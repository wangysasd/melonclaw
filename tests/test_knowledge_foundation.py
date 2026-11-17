"""向量知识库建库配置。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from melonclaw.api.routes.knowledge import CreateBase
from melonclaw.services.knowledge import KnowledgeService


def test_create_uses_fixed_embedding_and_default_chunk_config():
    async def run():
        model = uuid4()
        repo = SimpleNamespace(get_user_context=AsyncMock(return_value=object()),
            fixed_embedding_model_id=AsyncMock(return_value=model),
            knowledge_create=AsyncMock(return_value={"ok": True}))
        await KnowledgeService(repo, None).create("u", CreateBase(name="知识").model_dump())
        values = repo.knowledge_create.call_args.args[1]
        assert values["embedding_model_id"] == model
        assert values["chunk_config"] == {"chunk_size": 800, "chunk_overlap": 120}
    asyncio.run(run())
