"""发布和 token 事务只在显式隔离 test 库验证，不操作用户数据库。"""

import asyncio
import json
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select
from test_knowledge_management_postgres import create_base, isolated_repository

from melonclaw.core.knowledge import KnowledgeConflict, KnowledgeDenied
from melonclaw.database.schema import KNOWLEDGE_TABLES
from melonclaw.repository.mappers import _now

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 test PostgreSQL 及 pgvector"
)
bases, members, documents, revisions, texts, chunks, jobs, events = KNOWLEDGE_TABLES


async def uploaded(repo, base, actor="owner", doc_id=None):
    return await repo.knowledge_upload(
        actor,
        base["id"],
        document_id=doc_id or uuid4(),
        revision_id=uuid4(),
        name="test.txt",
        source_hash="a" * 64,
        source_byte_size=10,
        mime_type="text/plain",
        request_id=str(uuid4()),
        processing_spec={"extension": ".txt"},
        replace=doc_id is not None,
    )


def records(content):
    return [
        {
            "ordinal": 0,
            "content": content,
            "start_offset": 0,
            "end_offset": len(content),
            "source_locator": {"page": 1},
            "search_text": "测试 文档",
            "vector": json.dumps([1.0] + [0.0] * 1023),
        }
    ]


def test_publish_is_atomic_and_published_content_is_immutable():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            first = await uploaded(repo, base)
            job = await repo.knowledge_claim((await repo.knowledge_queue())[0])
            content = "中文👨‍👩‍👦e\u0301"
            original = repo._knowledge_event
            repo._knowledge_event = AsyncMock(side_effect=ValueError("audit failed"))
            with pytest.raises(ValueError):
                await repo.knowledge_publish(job, content, records(content))
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(texts)) == 0
                assert await conn.scalar(select(func.count()).select_from(chunks)) == 0
                assert (
                    await conn.scalar(
                        select(revisions.c.published_at).where(
                            revisions.c.id == first["revision_id"]
                        )
                    )
                    is None
                )
                assert await conn.scalar(select(documents.c.active_revision_id)) is None
                assert await conn.scalar(select(jobs.c.status)) == "running"
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(events)
                        .where(
                            events.c.target_id == str(first["revision_id"]),
                            events.c.action == "revision_published",
                        )
                    )
                    == 0
                )
            repo._knowledge_event = original
            await repo.knowledge_publish(job, content, records(content))
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(texts.c.content)) == content
                assert (
                    await conn.scalar(select(documents.c.active_revision_id))
                    == first["revision_id"]
                )
                assert (
                    await conn.scalar(
                        select(revisions.c.published_at).where(
                            revisions.c.id == first["revision_id"]
                        )
                    )
                    is not None
                )
                assert await conn.scalar(select(jobs.c.status)) == "succeeded"
                assert await conn.scalar(select(func.count()).select_from(chunks)) == 1
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(events)
                        .where(
                            events.c.target_id == str(first["revision_id"]),
                            events.c.action == "revision_published",
                        )
                    )
                    == 1
                )
            with pytest.raises(KnowledgeConflict):
                await repo.knowledge_publish(job, content, records(content))

    asyncio.run(run())


def test_cancelled_token_rejected_retry_uses_new_token_and_old_version_survives():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            first = await uploaded(repo, base)
            job = await repo.knowledge_claim((await repo.knowledge_queue())[0])
            await repo.knowledge_publish(job, "旧版", records("旧版"))
            await repo.knowledge_release_pipeline(job)
            await uploaded(repo, base, doc_id=first["document_id"])
            job = await repo.knowledge_claim((await repo.knowledge_queue())[0])
            await repo.knowledge_document_action(
                "owner", base["id"], first["document_id"], "cancel"
            )
            with pytest.raises(KnowledgeConflict):
                await repo.knowledge_publish(job, "迟到", records("迟到"))
            await repo.knowledge_release_pipeline(job)
            await repo.knowledge_document_action(
                "owner", base["id"], first["document_id"], "retry", str(uuid4())
            )
            new = await repo.knowledge_claim((await repo.knowledge_queue())[0])
            assert new["run_token"] != job["run_token"]
            await repo.knowledge_finish_failure(new)
            async with repo.engine.connect() as conn:
                assert (
                    await conn.scalar(select(documents.c.active_revision_id))
                    == first["revision_id"]
                )
                assert await conn.scalar(select(texts.c.content)) == "旧版"

    asyncio.run(run())


def test_viewer_and_cross_tenant_cannot_upload_or_cancel():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            await repo.knowledge_member_save("owner", base["id"], "viewer", "viewer")
            first = await uploaded(repo, base)
            for user in ["viewer", "outsider"]:
                with pytest.raises(KnowledgeDenied):
                    await uploaded(repo, base, user)
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_document_action(
                        user, base["id"], first["document_id"], "cancel"
                    )

    asyncio.run(run())


def test_document_list_selects_latest_source_jobs_with_stable_ties():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            original = await uploaded(repo, base)
            source_job = await repo.knowledge_claim((await repo.knowledge_queue())[0])
            await repo.knowledge_publish(source_job, '已发布正文', records('已发布正文'))
            await repo.knowledge_release_pipeline(source_job)
            missing = await uploaded(repo, base)
            now = _now()
            history = []
            for operation in ['replace', 'reparse']:
                for _ in range(30):
                    history.append(dict(
                        id=uuid4(), kb_id=base['id'], document_id=original['document_id'],
                        revision_id=original['revision_id'], operation=operation, requested_by='owner',
                        request_id=str(uuid4()), request_hash='a' * 64, status='failed',
                        error_code='test_failure', created_at=now, updated_at=now, finished_at=now,
                    ))
            async with repo.engine.begin() as conn:
                await conn.execute(insert(jobs), history)
            result = await repo.knowledge_documents('owner', base['id'], page_size=1)
            assert result['total'] == 2 and len(result['items']) == 1
            first_page_id = result['items'][0]['id']
            second_page = await repo.knowledge_documents('owner', base['id'], page=2, page_size=1)
            assert second_page['items'][0]['id'] != first_page_id
            listed = await repo.knowledge_documents('owner', base['id'])
            by_id = {item['id']: item for item in listed['items']}
            item = by_id[original['document_id']]
            latest = max(history, key=lambda row: row['id'])
            assert item['source_job']['id'] == latest['id']
            assert by_id[missing['document_id']]['source_job']['id'] == missing['id']

    asyncio.run(run())
