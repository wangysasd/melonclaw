"""仅使用名称含 test 的显式隔离库；独立 schema，不读取业务配置。"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.core.knowledge import KnowledgeConflict, KnowledgeDenied, KnowledgeForbidden
from melonclaw.core.knowledge_embedding import EMBEDDING_MODEL_KEY, EMBEDDING_PROVIDER_KEY
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.knowledge_ddl import ensure_vector
from melonclaw.database.knowledge_schema import SEARCH_VIEW_SQL
from melonclaw.database.model_ddl import verify_model_purpose
from melonclaw.database.schema import KNOWLEDGE_TABLES, metadata, tenants, users
from melonclaw.repository import BusinessRepository
from melonclaw.repository.mappers import _now
from melonclaw.services.knowledge import KnowledgeService

pytestmark = pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"),
    reason="需要隔离 test PostgreSQL 数据库及 pgvector",
)


@asynccontextmanager
async def isolated_repository():
    url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
    if "test" not in (make_url(url).database or "").lower():
        pytest.skip("数据库名称必须含 test；拒绝连接业务数据库")
    control = create_async_engine(url)
    schema = "test_knowledge_" + uuid4().hex
    async with control.begin() as conn:
        await ensure_vector(conn)
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
        execution_options={"schema_translate_map": {None: schema}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.execute(text(SEARCH_VIEW_SQL))
            await verify_model_purpose(conn)
            await conn.execute(
                insert(tenants),
                [
                    dict(tenant_id=t, tenant_name_zh=t, enabled=True, created_at=_now())
                    for t in ["team", "other"]
                ],
            )
            await conn.execute(
                insert(users),
                [
                    dict(
                        user_id=u,
                        user_name_zh=u,
                        tenant_id="other" if u == "outsider" else "team",
                        tenant_role="admin" if u == "owner" else "member",
                        created_at=_now(),
                    )
                    for u in ["admin", "owner", "editor", "viewer", "outsider", "target"]
                ],
            )
        repo = BusinessRepository(SimpleNamespace(engine=engine))
        await repo.create_provider_row(
            provider_key="provider",
            scope="global",
            source_type="manual",
            display_name="测试",
            provider_type="openai_compatible",
            base_url="https://example.com/v1",
            api_key="test-database-only",
            models_endpoint=None,
            created_by="owner",
            enabled=True,
        )
        for key, purpose in [
            ("chat", "chat"),
            ("embed", "embedding"),
            ("parser", "document_parser"),
        ]:
            await repo.create_model_row(
                model_key=key,
                provider_key="provider",
                scope="global",
                source_type="manual",
                display_name=key,
                model_name="BAAI/bge-m3" if purpose == "embedding" else key,
                purpose=purpose,
                context_window=10000 if purpose == "chat" else None,
                created_by="owner",
            )
            await repo.set_default_model(key)
        yield repo
    finally:
        await engine.dispose()
        async with control.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


async def create_base(repo, owner="owner", name="测试库"):
    return await KnowledgeService(
        repo,
        SimpleNamespace(
            knowledge_embedding_url="https://api.siliconflow.cn/v1",
            knowledge_embedding_model_name="BAAI/bge-m3",
            knowledge_embedding_dimensions=1024,
        ),
    ).create(
        owner,
        dict(
            name=name,
            description="",
            share_scope="selected",
            chunk_config={"chunk_size": 800, "chunk_overlap": 120},
        ),
    )


def test_system_admin_access_to_other_users_knowledge():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            kid = base["id"]
            await repo.knowledge_set_share_scope("owner", kid, base["version"], "selected")
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("outsider", kid)
            async with repo.engine.begin() as conn:
                await conn.execute(insert(tenants).values(tenant_id="system", tenant_name_zh="系统", enabled=True, created_at=_now()))
                await conn.execute(update(users).where(users.c.user_id == "admin").values(tenant_id="system"))
            row = await repo.knowledge_detail("admin", kid)
            assert row["role"] == "system_admin"
            assert any(item["id"] == kid for item in (await repo.knowledge_list("admin", page=1, page_size=20, search=""))["items"])
            await repo.knowledge_update("admin", kid, row["version"], {"description": "管理员修改"})
            async with repo.engine.begin() as conn:
                await conn.execute(update(users).where(users.c.user_id == "owner").values(tenant_status="deleted"))
            row = await repo.knowledge_detail("admin", kid)
            assert row["description"] == "管理员修改"
            await repo.knowledge_delete("admin", kid, row["version"])
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("admin", kid)
    asyncio.run(run())


async def running_job(repo, base_id, requested_by):
    _, _, document, revision, _, _, job, _ = KNOWLEDGE_TABLES
    did, rid, jid = uuid4(), uuid4(), uuid4()
    async with repo.engine.begin() as conn:
        await conn.execute(
            insert(document).values(
                id=did,
                kb_id=base_id,
                display_name="fixture",
                created_by=requested_by,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        await conn.execute(
            insert(revision).values(
                id=rid,
                kb_id=base_id,
                document_id=did,
                revision_no=1,
                source_key="fixture",
                source_hash="a" * 64,
                source_byte_size=1,
                mime_type="text/plain",
                processing_spec={},
                created_at=_now(),
            )
        )
        await conn.execute(
            insert(job).values(
                id=jid,
                kb_id=base_id,
                document_id=did,
                revision_id=rid,
                operation="upload",
                requested_by=requested_by,
                request_id=str(uuid4()),
                request_hash="b" * 64,
                status="running",
                stage="embedding",
                run_token=uuid4(),
                created_at=_now(),
                updated_at=_now(),
                started_at=_now(),
            )
        )
    return jid


def test_permission_transfer_models_and_cache_publication():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            kid = base["id"]
            for user, role in [("editor", "editor"), ("viewer", "viewer")]:
                await repo.knowledge_member_save("owner", kid, user, role)
                assert (await repo.knowledge_detail(user, kid))["role"] == role
            for user in ["editor", "viewer"]:
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_update(user, kid, 1, {"name": "Denied"})
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_members(user, kid)
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_delete(user, kid, 1)
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("outsider", kid)
            assert (await repo.knowledge_list("outsider", page=1, page_size=1, search=""))[
                "total"
            ] == 0
            with pytest.raises(ValueError):
                await repo.knowledge_member_save("owner", kid, "viewer", "manager")
            await repo.knowledge_member_save("owner", kid, "outsider", "viewer")
            assert (await repo.knowledge_detail("outsider", kid))["role"] == "viewer"
            with pytest.raises(ValueError):
                await repo.knowledge_member_save("owner", kid, "owner", "editor")
            await repo.knowledge_transfer("owner", kid, "target", 1)
            assert (await repo.knowledge_detail("target", kid))["role"] == "owner"
            assert (await repo.knowledge_detail("owner", kid))["role"] == "editor"
            async with repo.engine.connect() as conn:
                member = KNOWLEDGE_TABLES[1]
                assert (
                    await conn.scalar(
                        select(member.c.user_id).where(
                            member.c.kb_id == kid, member.c.user_id == "target"
                        )
                    )
                    is None
                )
            # 文档解析模型不被固定 OCR 引用；固定嵌入引用仍受保护。
            await repo.update_model_row("parser", model_name="changed", is_default=True)
            await repo.delete_model_row("parser")
            with pytest.raises(KnowledgeConflict):
                await repo.delete_model_row(EMBEDDING_MODEL_KEY)
            with pytest.raises(KnowledgeConflict):
                await repo.update_model_row(EMBEDDING_MODEL_KEY, model_name="changed")
            for changes in [
                {"base_url": "https://different.example/v1"},
                {"request_headers": {"X-Revision": "new"}},
                {"extra_config": {"dimensions": 512}},
            ]:
                with pytest.raises(KnowledgeConflict):
                    await repo.update_provider_row(EMBEDDING_PROVIDER_KEY, **changes)
            before = await repo.resource_versions.read("owner")
            await repo.update_provider_row(
                "provider", api_key="test-rotated", extra_config={"timeout": 60}
            )
            assert (await repo.resource_versions.read("owner"))[0] > before[0]
            assert await repo.get_system_default_model_key() == "chat"
            assert [r["model_key"] for r in await repo.list_visible_model_rows("owner")] == ["chat"]
            await repo.set_default_model("embed")
            assert (await repo.knowledge_detail("target", kid))["embedding_model_id"] == base[
                "embedding_model_id"
            ]
            with pytest.raises(KnowledgeConflict):
                await repo.delete_account_user("target")
            await repo.knowledge_delete("target", kid, 2)
            await repo.delete_account_user("target")
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("owner", kid)

    asyncio.run(run())


def test_tenant_move_keeps_named_grants_and_jobs():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            tombstone = await create_base(repo, name="已删除库")
            await repo.knowledge_member_save("owner", tombstone["id"], "viewer", "viewer")
            await repo.knowledge_delete("owner", tombstone["id"], 1)
            shared = await create_base(repo, "target", "共享给 owner")
            await repo.knowledge_member_save("owner", base["id"], "viewer", "viewer")
            await repo.knowledge_member_save("target", shared["id"], "owner", "editor")
            owned_job = await running_job(repo, base["id"], "owner")
            member_job = await running_job(repo, shared["id"], "owner")
            unaffected_base = await create_base(repo, "target", "另一份不受影响的库")
            unaffected = await running_job(repo, unaffected_base["id"], "target")
            await repo.save_account_user("owner", {"tenant_id": "other", "user_name_zh": "owner"})
            async with repo.engine.connect() as conn:
                member, job = KNOWLEDGE_TABLES[1], KNOWLEDGE_TABLES[6]
                assert (await conn.execute(select(member))).first()
                for jid in [owned_job, member_job, unaffected]:
                    assert await conn.scalar(select(job.c.status).where(job.c.id == jid)) == "running"
            assert (await repo.knowledge_detail("viewer", base["id"]))["role"] == "viewer"
            assert (await repo.knowledge_detail("owner", shared["id"]))["role"] == "editor"
            await repo.save_account_user("owner", {"tenant_id": "team", "user_name_zh": "owner"})
            assert (await repo.knowledge_detail("viewer", base["id"]))["role"] == "viewer"
            # 所有者停用后整库不可访问，包括已被点名的人。
            async with repo.engine.begin() as conn:
                await conn.execute(
                    update(users).where(users.c.user_id == "owner").values(tenant_status="deleted")
                )
            for user in ("owner", "viewer"):
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_detail(user, base["id"])

    asyncio.run(run())


def test_concurrent_binding_and_semantic_edit_are_serialized():
    async def run():
        async with isolated_repository() as repo:
            await repo.update_model_row("parser", is_default=False)
            result = await asyncio.wait_for(
                asyncio.gather(
                    create_base(repo),
                    repo.update_provider_row("provider", base_url="https://changed.example/v1"),
                    return_exceptions=True,
                ),
                10,
            )
            assert isinstance(result[0], dict)
            # 编辑先发生则绑定到新端点；绑定先发生则编辑明确冲突。
            assert result[1] is None or isinstance(result[1], KnowledgeConflict)
            # 审计与业务一起回滚，不留下半条授权。
            original = repo._knowledge_event

            async def fail(*a, **kw):
                raise RuntimeError("test rollback")

            repo._knowledge_event = fail
            try:
                with pytest.raises(RuntimeError):
                    await repo.knowledge_member_save("owner", result[0]["id"], "viewer", "viewer")
            finally:
                repo._knowledge_event = original
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("viewer", result[0]["id"])

    asyncio.run(run())


def test_concurrent_migration_and_grant_leave_no_old_authorization():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            result = await asyncio.wait_for(
                asyncio.gather(
                    repo.knowledge_member_save("owner", base["id"], "viewer", "viewer"),
                    repo.save_account_user(
                        "owner", {"tenant_id": "other", "user_name_zh": "owner"}
                    ),
                    return_exceptions=True,
                ),
                10,
            )
            assert result[0] is None or isinstance(result[0], KnowledgeDenied)
            assert result[1] is None
            await repo.save_account_user("owner", {"tenant_id": "team", "user_name_zh": "owner"})
            if result[0] is None:
                assert (await repo.knowledge_detail("viewer", base["id"]))["role"] == "viewer"
            else:
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_detail("viewer", base["id"])

    asyncio.run(run())


def test_share_scope_is_live_and_keeps_named_roles():
    async def run():
        async with isolated_repository() as repo:
            base = await create_base(repo)
            kid, version = base["id"], base["version"]
            assert base["share_scope"] == "selected"
            for user in ("viewer", "outsider"):
                with pytest.raises(KnowledgeDenied):
                    await repo.knowledge_detail(user, kid)
            scoped = await repo.knowledge_set_share_scope("owner", kid, version, "tenant")
            assert (await repo.knowledge_detail("viewer", kid))["role"] == "viewer"
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("outsider", kid)
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_update("viewer", kid, scoped["version"], {"name": "越权"})
            opened = await repo.knowledge_set_share_scope("owner", kid, scoped["version"], "everyone")
            assert (await repo.knowledge_detail("outsider", kid))["role"] == "viewer"
            await repo.knowledge_member_save("owner", kid, "outsider", "editor")
            assert (await repo.knowledge_detail("outsider", kid))["role"] == "editor"
            named = await repo.knowledge_set_share_scope("owner", kid, opened["version"], "selected")
            assert named["share_scope"] == "selected"
            assert (await repo.knowledge_detail("outsider", kid))["role"] == "editor"
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("viewer", kid)
            async with repo.engine.begin() as conn:
                await conn.execute(update(tenants).where(tenants.c.tenant_id == "team").values(enabled=False))
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("outsider", kid)

    asyncio.run(run())


def test_admin_operation_history_is_scoped_and_does_not_reopen_content():
    async def run():
        async with isolated_repository() as repo:
            first = await create_base(repo, name="甲库")
            second = await create_base(repo, name="乙库")
            kid = first["id"]
            scoped = await repo.knowledge_set_share_scope("owner", kid, first["version"], "tenant")
            opened = await repo.knowledge_set_share_scope("owner", kid, scoped["version"], "everyone")
            described = await repo.knowledge_update(
                "owner", kid, opened["version"], {"description": "secret-description-text"}
            )
            documents = KNOWLEDGE_TABLES[2]
            async with repo.engine.begin() as conn:
                await conn.execute(
                    insert(documents).values(
                        id=uuid4(),
                        kb_id=kid,
                        display_name="fixture-original-name",
                        created_by="owner",
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )
                await repo._knowledge_event(conn, kid, "owner", "document_deleted", "document", uuid4())
            with pytest.raises(KnowledgeForbidden):
                await repo.knowledge_operation_history("owner", kid, page=1, page_size=20)
            with pytest.raises(KnowledgeForbidden):
                await repo.knowledge_operation_history("admin", kid, page=1, page_size=20)
            async with repo.engine.begin() as conn:
                await conn.execute(
                    insert(tenants).values(
                        tenant_id="system", tenant_name_zh="系统", enabled=True, created_at=_now()
                    )
                )
                await conn.execute(update(users).where(users.c.user_id == "admin").values(tenant_id="system"))
            history = await repo.knowledge_operation_history("admin", kid, page=1, page_size=20)
            changes = [item for item in history["items"] if item["detail"] == "同租户 → 所有用户"]
            assert len(changes) == 1
            assert changes[0]["actor_user_id"] == "owner"
            assert changes[0]["actor_label"] == "owner（owner）"
            assert changes[0]["kb_name"] == "甲库"
            assert changes[0]["created_at"] is not None
            other = await repo.knowledge_operation_history("admin", second["id"], page=1, page_size=20)
            assert other["items"]
            assert all(item["kb_id"] == second["id"] for item in other["items"])
            assert all(item["detail"] != "同租户 → 所有用户" for item in other["items"])
            suspended = await repo.knowledge_suspend("admin", kid, described["version"], "report_review")
            removed = await repo.knowledge_dispose(
                "admin", kid, suspended["version"], "confirmed_violation"
            )
            assert removed["status"] == "deleted"
            first_page = await repo.knowledge_operation_history("admin", kid, page=1, page_size=1)
            assert first_page["status"] == "deleted"
            assert first_page["total"] > 1
            assert first_page["items"][0]["action"] == "base_deleted"
            assert first_page["items"][0]["detail"] == "确认违规，删除知识库"
            next_page = await repo.knowledge_operation_history("admin", kid, page=2, page_size=1)
            assert next_page["items"][0]["id"] != first_page["items"][0]["id"]
            full = await repo.knowledge_operation_history("admin", kid, page=1, page_size=20)
            assert any(
                item["action"] == "base_suspended" and item["detail"] == "接到举报，暂停核查"
                for item in full["items"]
            )
            assert all(item["action"] != "document_deleted" for item in full["items"])
            blob = json.dumps(full, default=str)
            assert "secret-description-text" not in blob
            assert "fixture-original-name" not in blob
            assert "safe_details" not in blob
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("admin", kid)
            with pytest.raises(KnowledgeDenied):
                await repo.knowledge_detail("owner", kid)
            current = await repo.knowledge_detail("owner", second["id"])
            await repo.knowledge_delete("owner", second["id"], current["version"])
            second_history = await repo.knowledge_operation_history("admin", second["id"], page=1, page_size=20)
            deleted = [item for item in second_history["items"] if item["action"] == "base_deleted"]
            assert deleted[0]["detail"] == "未记录"
            assert second_history["status"] == "deleted"

    asyncio.run(run())
