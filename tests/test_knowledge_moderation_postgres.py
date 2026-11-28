"""知识库停用、恢复和处置删除只在名称含 test 的隔离库验证。"""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text, update
from test_knowledge_ingestion_postgres import uploaded
from test_knowledge_management_postgres import create_base, isolated_repository, running_job
from test_knowledge_search_postgres import publish

from melonclaw.core.knowledge import (
    KnowledgeConflict,
    KnowledgeDenied,
    KnowledgeError,
    KnowledgeForbidden,
)
from melonclaw.database.schema import KNOWLEDGE_TABLES, tenants, users
from melonclaw.database.updates.knowledge_moderation import upgrade as moderate_events
from melonclaw.repository.mappers import _now
from melonclaw.services.knowledge_search import ChatKnowledgeProvider

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 test PostgreSQL 数据库及 pgvector",
)

bases, members, documents, revisions, texts, chunks, jobs, events = KNOWLEDGE_TABLES


async def promote_admin(repo):
    async with repo.engine.begin() as conn:
        if await conn.scalar(select(tenants.c.tenant_id).where(tenants.c.tenant_id == "system")) is None:
            await conn.execute(insert(tenants).values(
                tenant_id="system", tenant_name_zh="系统", enabled=True, created_at=_now(),
            ))
        await conn.execute(update(users).where(users.c.user_id == "admin").values(tenant_id="system"))


async def share_tenant(repo, base):
    return await repo.knowledge_set_share_scope("owner", base["id"], base["version"], "tenant")


def provider(repo):
    return ChatKnowledgeProvider(SimpleNamespace(require_ready=lambda: repo, settings=SimpleNamespace()))


async def job_status(repo, job_id):
    async with repo.engine.connect() as conn:
        return await conn.scalar(select(jobs.c.status).where(jobs.c.id == job_id))


def test_suspend_hides_one_library_and_leaves_the_other_usable():
    async def run():
        async with isolated_repository() as repo:
            library_a = await share_tenant(repo, await create_base(repo, name="库A"))
            library_b = await share_tenant(repo, await create_base(repo, name="库B"))
            await repo.knowledge_member_save("owner", library_a["id"], "editor", "editor")
            published_a = await publish(
                repo, library_a, "kbalpha evidence", search_text="kbalpha evidence",
            )
            published_b = await publish(
                repo, library_b, "kbbeta evidence", search_text="kbbeta evidence",
            )
            early = (await repo.knowledge_detail("owner", library_a["id"]))["version"]
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_suspend("admin", library_a["id"], early, "report_review")
            await promote_admin(repo)
            for user in ("viewer", "editor", "owner", "admin"):
                listed = {item["id"] for item in (await repo.knowledge_list(user, page=1, page_size=20, search=""))["items"]}
                assert library_a["id"] in listed and library_b["id"] in listed
            chat = provider(repo)
            context = SimpleNamespace(user_id="viewer", knowledge_base_ids=[])
            names = {item["name"] for item in (await chat.list_bases(context))["items"]}
            assert names == {"库A", "库B"}
            context.knowledge_base_ids = [str(library_b["id"])]
            found = await chat.search(context, str(library_b["id"]), {
                "query": "kbbeta", "mode": "keyword", "top_k": 5, "document_ids": [],
            })
            assert found["items"] and "kbbeta" in found["items"][0]["content"]
            opened = await repo.knowledge_open_text(
                "viewer", library_b["id"], published_b["document_id"], published_b["revision_id"],
            )
            assert "kbbeta" in opened["content"]
            assert (await repo.knowledge_source("viewer", library_b["id"], published_b["document_id"]))["id"] == published_b["revision_id"]

            version = (await repo.knowledge_detail("owner", library_a["id"]))["version"]
            for user in ("viewer", "editor", "owner"):
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_suspend(user, library_a["id"], version, "report_review")
            with pytest.raises(KnowledgeError):
                await repo.knowledge_suspend("admin", library_a["id"], version, "把正文写进原因")
            await repo.knowledge_suspend("admin", library_a["id"], version, "report_review")
            with pytest.raises(KnowledgeConflict):
                await repo.knowledge_suspend("admin", library_a["id"], version, "report_review")

            for user in ("viewer", "editor", "owner", "admin"):
                listed = {item["id"] for item in (await repo.knowledge_list(user, page=1, page_size=20, search=""))["items"]}
                assert library_a["id"] not in listed and library_b["id"] in listed
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_detail(user, library_a["id"])
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_open_text(
                        user, library_a["id"], published_a["document_id"], published_a["revision_id"],
                    )
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_source(user, library_a["id"], published_a["document_id"])
            context.knowledge_base_ids = [str(library_a["id"])]
            with pytest.raises(KnowledgeDenied):
                await chat.search(context, str(library_a["id"]), {
                    "query": "kbalpha", "mode": "keyword", "top_k": 5, "document_ids": [],
                })
            hidden = await chat.service().describe_selection("viewer", [str(library_a["id"])])
            assert hidden[0]["name"] == "当前不可访问"
            context.knowledge_base_ids = [str(library_b["id"])]
            still = await chat.search(context, str(library_b["id"]), {
                "query": "kbbeta", "mode": "keyword", "top_k": 5, "document_ids": [],
            })
            assert still["items"] and "kbbeta" in still["items"][0]["content"]
            async with repo.engine.connect() as conn:
                assert await conn.scalar(text(
                    "SELECT count(*) FROM knowledge_search_chunks WHERE kb_id = :kb"
                ), {"kb": library_a["id"]}) == 0
                assert await conn.scalar(text(
                    "SELECT count(*) FROM knowledge_search_chunks WHERE kb_id = :kb"
                ), {"kb": library_b["id"]}) == 1
                assert await conn.scalar(select(func.count()).select_from(texts).where(texts.c.kb_id == library_a["id"])) == 1
            suspended = await repo.knowledge_moderation_list("admin", page=1, page_size=20, search="")
            assert [item["id"] for item in suspended["items"]] == [library_a["id"]]
            assert "kbalpha" not in str(suspended)
            for user in ("viewer", "editor", "owner"):
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_moderation_list(user, page=1, page_size=20, search="")
                with pytest.raises(KnowledgeForbidden):
                    await repo.knowledge_operation_history(user, library_a["id"], page=1, page_size=20)

    asyncio.run(run())


def test_restore_uses_current_grants_and_deleted_library_keeps_history():
    async def run():
        async with isolated_repository() as repo:
            library = await share_tenant(repo, await create_base(repo, name="库A"))
            await repo.knowledge_member_save("owner", library["id"], "editor", "editor")
            queued = await uploaded(repo, library)
            await promote_admin(repo)
            version = (await repo.knowledge_detail("owner", library["id"]))["version"]
            await repo.knowledge_suspend("admin", library["id"], version, "risk_hold")
            assert await job_status(repo, queued["id"]) == "cancelled"
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(members.c.role).where(
                    members.c.kb_id == library["id"], members.c.user_id == "editor",
                )) == "editor"
            await repo.save_account_user("owner", {"tenant_id": "other", "user_name_zh": "owner"})
            suspended = (await repo.knowledge_moderation_list("admin", page=1, page_size=20, search="库A"))["items"][0]
            await repo.knowledge_restore("admin", library["id"], suspended["version"], "review_passed")
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("viewer", library["id"])
            assert (await repo.knowledge_detail("outsider", library["id"]))["role"] == "viewer"
            assert (await repo.knowledge_detail("editor", library["id"]))["role"] == "editor"
            assert (await repo.knowledge_detail("owner", library["id"]))["share_scope"] == "tenant"
            assert await job_status(repo, queued["id"]) == "cancelled"
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(members).where(
                    members.c.kb_id == library["id"], members.c.user_id == "outsider",
                )) == 0
            current = (await repo.knowledge_detail("admin", library["id"]))["version"]
            await repo.knowledge_suspend("admin", library["id"], current, "report_review")
            removed = (await repo.knowledge_moderation_list("admin", page=1, page_size=20, search=""))["items"][0]
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_delete("admin", library["id"], removed["version"])
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_dispose("editor", library["id"], removed["version"], "confirmed_violation")
            await repo.knowledge_dispose("admin", library["id"], removed["version"], "confirmed_violation")
            with pytest.raises(KnowledgeConflict, match="不能恢复"):
                await repo.knowledge_restore("admin", library["id"], removed["version"] + 1, "review_passed")
            history = await repo.knowledge_operation_history("admin", library["id"], page=1, page_size=20)
            assert history["status"] == "deleted"
            details = [item["detail"] for item in history["items"] if item["action"] in {"base_suspended", "base_restored", "base_deleted"}]
            assert details == ["确认违规，删除知识库", "接到举报，暂停核查", "复核通过，恢复共享", "发现风险，暂停共享"]
            assert "kbalpha" not in str(history)
            async with repo.engine.connect() as conn:
                stored = (await conn.execute(select(events.c.safe_details).where(events.c.kb_id == library["id"]))).scalars().all()
                assert {"reason": "confirmed_violation"} in stored
                assert await conn.scalar(select(func.count()).select_from(documents).where(documents.c.kb_id == library["id"])) == 1
                assert await conn.scalar(select(bases.c.status).where(bases.c.id == library["id"])) == "deleted"

    asyncio.run(run())


def test_suspend_cancels_only_that_library_and_publish_rechecks_visibility():
    async def run():
        async with isolated_repository() as repo:
            library_a = await create_base(repo, name="库A")
            library_b = await create_base(repo, name="库B")
            running_a = await running_job(repo, library_a["id"], "owner")
            running_b = await running_job(repo, library_b["id"], "owner")
            queued_base = await create_base(repo, name="库C")
            queued = await uploaded(repo, queued_base)
            await promote_admin(repo)
            version_a = (await repo.knowledge_detail("owner", library_a["id"]))["version"]
            version_c = (await repo.knowledge_detail("owner", queued_base["id"]))["version"]
            await repo.knowledge_suspend("admin", library_a["id"], version_a, "report_review")
            await repo.knowledge_suspend("admin", queued_base["id"], version_c, "report_review")
            assert await job_status(repo, running_a) == "cancelled"
            assert await job_status(repo, running_b) == "running"
            assert await job_status(repo, queued["id"]) == "cancelled"
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(bases.c.active_pipeline_job_id).where(bases.c.id == queued_base["id"])) is None
            from test_knowledge_ingestion_postgres import records
            held = await create_base(repo, name="库D")
            request = await uploaded(repo, held)
            claimed = await repo.knowledge_claim(next(item for item in await repo.knowledge_queue() if item["id"] == request["id"]))
            async with repo.engine.begin() as conn:
                await conn.execute(update(bases).where(bases.c.id == held["id"]).values(status="suspended", updated_at=_now()))
            content = "pending body"
            with pytest.raises(KnowledgeConflict):
                await repo.knowledge_publish(claimed, content, records(content))
            assert await job_status(repo, claimed["id"]) == "running"
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_job_check(claimed)
            assert await job_status(repo, claimed["id"]) == "running"
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(texts).where(texts.c.kb_id == held["id"])) == 0
            late = await create_base(repo, name="库E")
            waiting = await uploaded(repo, late)
            queued_row = next(item for item in await repo.knowledge_queue() if item["id"] == waiting["id"])
            async with repo.engine.begin() as conn:
                await conn.execute(update(bases).where(bases.c.id == late["id"]).values(status="suspended", updated_at=_now()))
            assert await repo.knowledge_claim(queued_row) is None
            assert await job_status(repo, waiting["id"]) == "cancelled"

    asyncio.run(run())


def test_concurrent_moderation_and_audit_failure_stay_consistent():
    async def run():
        async with isolated_repository() as repo:
            library = await create_base(repo, name="并发库")
            await promote_admin(repo)
            version = library["version"]
            outcome = await asyncio.wait_for(asyncio.gather(
                repo.knowledge_suspend("admin", library["id"], version, "report_review"),
                repo.knowledge_suspend("admin", library["id"], version, "risk_hold"),
                return_exceptions=True,
            ), 10)
            assert sum(not isinstance(item, Exception) for item in outcome) == 1
            assert any(isinstance(item, KnowledgeConflict) for item in outcome)
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(events).where(
                    events.c.kb_id == library["id"], events.c.action == "base_suspended",
                )) == 1
                assert await conn.scalar(select(bases.c.status).where(bases.c.id == library["id"])) == "suspended"
            current = (await repo.knowledge_moderation_list("admin", page=1, page_size=20, search="并发"))["items"][0]
            removed = await asyncio.wait_for(asyncio.gather(
                repo.knowledge_dispose("admin", library["id"], current["version"], "confirmed_violation"),
                repo.knowledge_restore("admin", library["id"], current["version"], "review_passed"),
                return_exceptions=True,
            ), 10)
            success = [item for item in removed if not isinstance(item, Exception)]
            assert len(success) == 1
            async with repo.engine.connect() as conn:
                status = await conn.scalar(select(bases.c.status).where(bases.c.id == library["id"]))
                actions = (await conn.execute(select(events.c.action).where(
                    events.c.kb_id == library["id"],
                    events.c.action.in_(["base_suspended", "base_restored", "base_deleted"]),
                ))).scalars().all()
            assert len(success) == 1
            if success[0]["status"] == "deleted":
                assert status == "deleted"
                assert actions.count("base_deleted") == 1
                assert "base_restored" not in actions
            else:
                assert success[0]["status"] == "active"
                assert status == "active"
                assert actions.count("base_restored") == 1
                assert "base_deleted" not in actions

            other = await create_base(repo, name="回滚库")
            original = repo._knowledge_event

            async def fail(*args, **kwargs):
                raise RuntimeError("audit failed")

            repo._knowledge_event = fail
            try:
                with pytest.raises(RuntimeError):
                    await repo.knowledge_suspend("admin", other["id"], other["version"], "report_review")
            finally:
                repo._knowledge_event = original
            assert (await repo.knowledge_detail("owner", other["id"]))["status"] == "active"
            async with repo.engine.connect() as conn:
                assert await conn.scalar(select(func.count()).select_from(events).where(
                    events.c.kb_id == other["id"], events.c.action == "base_suspended",
                )) == 0

    asyncio.run(run())


def test_event_action_upgrade_adds_moderation_without_rewriting_rows():
    async def run():
        async with isolated_repository() as repo:
            library = await create_base(repo)
            async with repo.engine.begin() as conn:
                await conn.execute(text("ALTER TABLE knowledge_events DROP CONSTRAINT ck_kb_event_action"))
                await conn.execute(text(
                    "ALTER TABLE knowledge_events ADD CONSTRAINT ck_kb_event_action "
                    "CHECK (action IN ('base_created','base_deleted','base_updated',"
                    "'ownership_transferred','share_scope_changed','member_granted',"
                    "'member_updated','member_revoked','document_deleted','revision_published'))"
                ))
                before = await conn.scalar(select(func.count()).select_from(events))
                async with conn.begin_nested() as nested:
                    with pytest.raises(Exception, match="ck_kb_event_action"):
                        await conn.execute(insert(events).values(
                            id=uuid4(), kb_id=library["id"], actor_user_id="owner",
                            action="base_suspended", target_type="base", target_id=str(library["id"]),
                            safe_details={"reason": "report_review"}, created_at=_now(),
                        ))
                    await nested.rollback()
                await moderate_events(conn)
                await conn.execute(insert(events).values(
                    id=uuid4(), kb_id=library["id"], actor_user_id="owner",
                    action="base_suspended", target_type="base", target_id=str(library["id"]),
                    safe_details={"reason": "report_review"}, created_at=_now(),
                ))
                await moderate_events(conn)
                definition = await conn.scalar(text(
                    "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE c.conname = 'ck_kb_event_action' AND n.nspname = current_schema()"
                ))
                assert "base_suspended" in definition and "base_restored" in definition
                assert "base_created" in definition
                assert await conn.scalar(select(func.count()).select_from(events)) == before + 1
                async with conn.begin_nested() as nested:
                    with pytest.raises(Exception, match="ck_kb_event_action"):
                        await conn.execute(insert(events).values(
                            id=uuid4(), kb_id=library["id"], actor_user_id="owner",
                            action="not_an_event", target_type="base", target_id=str(library["id"]),
                            safe_details={}, created_at=_now(),
                        ))
                    await nested.rollback()

    asyncio.run(run())
