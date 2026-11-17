"""真实数据库的知识库并发与退出保护。"""
import asyncio
import os
from uuid import uuid4

import pytest
from test_knowledge_ingestion_postgres import records, uploaded
from test_knowledge_management_postgres import create_base, isolated_repository

from melonclaw.core.knowledge import KnowledgeConflict

pytestmark = pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离test PostgreSQL")


def test_single_base_slot_idempotency_cancel_exit_and_recovery():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            # 相同库的真实并发事务只能登记一个queued。
            results = await asyncio.gather(uploaded(repo, base), uploaded(repo, base), return_exceptions=True)
            first = next(r for r in results if isinstance(r, dict))
            assert sum(isinstance(r, KnowledgeConflict) for r in results) == 1
            queued = (await repo.knowledge_queue())[0]
            replay = await repo.knowledge_upload("owner", base["id"], document_id=uuid4(), revision_id=uuid4(), name="test.txt",
                source_hash="a" * 64, source_byte_size=10, mime_type="text/plain", request_id=queued["request_id"], processing_spec={"extension": ".txt"})
            assert replay["id"] == first["id"]
            claimed = await repo.knowledge_claim(queued)
            await repo.knowledge_document_action("owner", base["id"], first["document_id"], "cancel")
            with pytest.raises(KnowledgeConflict):
                await uploaded(repo, base)
            with pytest.raises(KnowledgeConflict):
                await repo.knowledge_document_action("owner", base["id"], first["document_id"], "retry", str(uuid4()))
            await repo.knowledge_release_pipeline(claimed)
            second = await uploaded(repo, base)
            # queued取消无实际进程，可以立即释放。
            await repo.knowledge_document_action("owner", base["id"], second["document_id"], "cancel")
            third = await uploaded(repo, base)
            job = await repo.knowledge_claim((await repo.knowledge_queue())[0])
            await repo.knowledge_publish(job, "原文", records("原文"))
            with pytest.raises(KnowledgeConflict):
                await uploaded(repo, base)
            await repo.knowledge_release_pipeline(job)
            assert (await repo.knowledge_detail("owner", base["id"]))["active_pipeline_job_id"] is None
            fourth = await uploaded(repo, base)
            await repo.knowledge_claim((await repo.knowledge_queue())[0])
            await repo.knowledge_recover()
            assert (await repo.knowledge_detail("owner", base["id"]))["active_pipeline_job_id"] is None
            assert first["id"] != fourth["id"] != third["id"]
    asyncio.run(run())

