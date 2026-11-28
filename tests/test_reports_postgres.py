"""真实 PostgreSQL 上的投诉举报验收。没有测试库时跳过。"""

import asyncio
import hashlib
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from account_fixtures import use_access_token
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from test_knowledge_ingestion_postgres import records

from melonclaw.api.app import create_app
from melonclaw.core.config import load_settings
from melonclaw.core.knowledge_embedding import EMBEDDING_MODEL_ID
from melonclaw.core.passwords import hash_password
from melonclaw.core.reports import content_digest
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import (
    KNOWLEDGE_TABLES,
    chat_conversations,
    chat_messages,
    metadata,
    model_configs,
    model_providers,
    projects,
    report_events,
    reports,
    tenants,
    users,
)
from melonclaw.database.update_runner import apply_schema_updates, record_initialized_schema
from melonclaw.repository import BusinessRepository
from melonclaw.repository.mappers import _now

bases, _members, documents, revisions, texts, _chunks, _jobs, _events = KNOWLEDGE_TABLES


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接")
def test_report_postgres_acceptance(monkeypatch, tmp_path):
    monkeypatch.setenv("profile", "dev")
    monkeypatch.setenv("MELONCLAW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MELONCLAW_WORKSPACE_DIR", str(tmp_path / "workspace"))

    async def run():
        url = normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"])
        schema = "test_reports_" + uuid4().hex
        control = create_async_engine(url)
        async with control.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            url, connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(lambda sync: metadata.create_all(
                    sync, tables=[table for table in metadata.tables.values()
                                  if table.name not in {"reports", "report_events"}],
                ))
                await record_initialized_schema(conn)
                await conn.execute(text(
                    "DELETE FROM melonclaw_schema_updates WHERE update_id='20261126_reports'"
                ))
            database = SimpleNamespace(engine=engine)
            repo = BusinessRepository(database)
            now = _now()
            password = hash_password("password-123")
            alice_conversation, alice_message = uuid4(), uuid4()
            bob_conversation, bob_message = uuid4(), uuid4()
            kb_id, document_id, revision_id = uuid4(), uuid4(), uuid4()
            provider_id, model_id = uuid4(), EMBEDDING_MODEL_ID
            archived_project, archived_conversation, archived_message = uuid4(), uuid4(), uuid4()
            old_text = "OLD-DOC-SECRET"
            new_text = "NEW-DOC-SECRET"
            source_hash = "a" * 64
            async with engine.begin() as conn:
                await conn.execute(insert(tenants).values(
                    tenant_id="system", tenant_name_zh="系统", created_at=now, updated_at=now,
                ))
                await conn.execute(insert(tenants).values(
                    tenant_id="team", tenant_name_zh="团队", created_at=now, updated_at=now,
                ))
                for user_id, name, tenant_id, role in (
                    ("admin", "管理员", "system", "owner"),
                    ("alice", "爱丽丝", "team", "member"),
                    ("bob", "鲍勃", "team", "member"),
                ):
                    await conn.execute(insert(users).values(
                        user_id=user_id, tenant_id=tenant_id, user_name_zh=name, tenant_role=role,
                        password_hash=password, created_at=now, updated_at=now,
                    ))
                await conn.execute(insert(model_providers).values(
                    id=provider_id, provider_key="embed-test", scope="global", source_type="system",
                    display_name="嵌入", base_url="https://example.invalid", created_by="admin",
                    created_at=now, updated_at=now,
                ))
                await conn.execute(insert(model_configs).values(
                    id=model_id, model_key="embed-test-model", provider_key="embed-test",
                    scope="global", purpose="embedding", source_type="system",
                    display_name="嵌入模型", model_name="embed", created_by="admin",
                    created_at=now, updated_at=now,
                ))
                await conn.execute(insert(projects).values(
                    id=archived_project, user_id="alice", status="deleted", name="已删除项目",
                    workdir_path="archived-test-project", created_at=now, updated_at=now,
                ))
                for user_id, conversation_id, message_id, content in (
                    ("alice", alice_conversation, alice_message, "ALICE-HEAD " + ("x" * 300) + " ALICE-TAIL"),
                    ("bob", bob_conversation, bob_message, "BOB-PRIVATE"),
                    ("alice", archived_conversation, archived_message, "ARCHIVED-PRIVATE"),
                ):
                    await conn.execute(insert(chat_conversations).values(
                        id=conversation_id, user_id=user_id, created_at=now, updated_at=now,
                        project_id=archived_project if conversation_id == archived_conversation else None,
                    ))
                    await conn.execute(insert(chat_messages).values(
                        id=message_id, conversation_id=conversation_id, seq=2, request_id=str(uuid4()),
                        role="assistant", content=content, status="completed",
                        created_at=now, updated_at=now,
                    ))
                await conn.execute(insert(bases).values(
                    id=kb_id, owner_user_id="alice", name="共享库", embedding_model_id=model_id,
                    chunk_config={}, share_scope="everyone", created_at=now, updated_at=now,
                ))
                await conn.execute(insert(documents).values(
                    id=document_id, kb_id=kb_id, display_name="制度", created_by="alice",
                    created_at=now, updated_at=now,
                ))
                await conn.execute(insert(revisions).values(
                    id=revision_id, kb_id=kb_id, document_id=document_id, revision_no=1,
                    source_key="source-v1", source_hash=source_hash, source_byte_size=len(old_text),
                    mime_type="text/plain", processing_spec={}, created_at=now, published_at=now,
                ))
                await conn.execute(insert(texts).values(
                    revision_id=revision_id, kb_id=kb_id, content=old_text,
                    text_hash=content_digest(old_text), char_count=len(old_text),
                ))
                await conn.execute(update(documents).where(documents.c.id == document_id).values(
                    active_revision_id=revision_id,
                ))
            assert await apply_schema_updates(database) == ("20261126_reports",)
            assert await apply_schema_updates(database) == ()
            app = create_app()
            app.state.chat = SimpleNamespace(
                storage=repo, runtime=SimpleNamespace(settings=load_settings(), require_ready=lambda: repo),
            )
            app.state.knowledge_executor = SimpleNamespace(ready=True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                created = await client.post("/api/reports/anonymous", json={
                    "reason": "cannot_login", "description": "无法登录", "contact": "guest@example.com",
                })
                assert created.status_code == 201, created.text
                ticket = created.json()
                async with engine.connect() as conn:
                    stored = (await conn.execute(
                        select(reports.c.lookup_code_hash).where(reports.c.public_code == ticket["public_code"])
                    )).scalar_one()
                assert stored == hashlib.sha256(ticket["lookup_code"].encode()).hexdigest()
                assert stored != ticket["lookup_code"]
                found = await client.post("/api/reports/lookup", json={
                    "public_code": ticket["public_code"], "lookup_code": ticket["lookup_code"],
                })
                assert found.status_code == 200
                wrong = await client.post("/api/reports/lookup", json={
                    "public_code": ticket["public_code"], "lookup_code": "wrong-lookup-code",
                })
                assert wrong.status_code == 404 and "无法登录" not in wrong.text
                code_only = await client.post("/api/reports/lookup", json={"public_code": ticket["public_code"]})
                assert code_only.status_code == 404 and code_only.json() == wrong.json()
                other = await client.post("/api/reports/anonymous", json={
                    "reason": "other", "description": "另一位访客的说明",
                })
                assert other.status_code == 201
                foreign = await client.post("/api/reports/lookup", json={
                    "public_code": other.json()["public_code"], "lookup_code": ticket["lookup_code"],
                })
                assert foreign.status_code == 404 and foreign.json() == wrong.json()
                alice = await client.post("/api/auth/login", json={"user_id": "alice", "password": "password-123"})
                use_access_token(client, alice)
                denied = await client.post("/api/reports", json={
                    "reason": "harmful", "description": "越权",
                    "target": {
                        "kind": "assistant_message",
                        "conversation_id": str(bob_conversation), "message_id": str(bob_message),
                    },
                })
                assert denied.status_code == 404 and "BOB-PRIVATE" not in denied.text
                archived = await client.post("/api/reports", json={
                    "reason": "harmful", "description": "项目已删除",
                    "target": {
                        "kind": "assistant_message",
                        "conversation_id": str(archived_conversation), "message_id": str(archived_message),
                    },
                })
                assert archived.status_code == 404
                own = await client.post("/api/reports", json={
                    "reason": "harmful", "description": "自己的回复",
                    "target": {
                        "kind": "assistant_message",
                        "conversation_id": str(alice_conversation), "message_id": str(alice_message),
                    },
                })
                assert own.status_code == 201, own.text
                bob = await client.post("/api/auth/login", json={"user_id": "bob", "password": "password-123"})
                use_access_token(client, bob)
                hidden = await client.get(f"/api/reports/{own.json()['public_code']}")
                assert hidden.status_code == 404
                reported = await client.post("/api/reports", json={
                    "reason": "privacy", "description": "文档有问题",
                    "target": {
                        "kind": "knowledge_document", "kb_id": str(kb_id),
                        "document_id": str(document_id), "revision_id": str(revision_id),
                    },
                })
                assert reported.status_code == 201, reported.text
                use_access_token(client, alice)
                uploaded = await client.post(
                    f"/api/knowledge-bases/{kb_id}/documents",
                    files={"file": ("制度.txt", new_text.encode(), "text/plain")},
                    data={"request_id": str(uuid4()), "document_id": str(document_id)},
                )
                assert uploaded.status_code == 202, uploaded.text
                job = await repo.knowledge_claim((await repo.knowledge_queue())[0])
                assert str(job["revision_id"]) == uploaded.json()["revision_id"]
                await repo.knowledge_publish(job, new_text, records(new_text))
                await repo.knowledge_release_pipeline(job)
                current = await client.get(f"/api/knowledge-bases/{kb_id}/documents/{document_id}/text")
                assert current.json()["content"] == new_text
                details = await client.get(f"/api/knowledge-bases/{kb_id}")
                changed = await client.patch(f"/api/knowledge-bases/{kb_id}", json={
                    "version": details.json()["version"], "share_scope": "selected",
                })
                assert changed.status_code == 200, changed.text
                admin = await client.post("/api/auth/login", json={"user_id": "admin", "password": "password-123"})
                use_access_token(client, admin)
                listed = await client.get("/api/admin/reports", params={"code": reported.json()["public_code"]})
                assert listed.status_code == 200
                report_id = listed.json()["items"][0]["id"]
                detail = await client.get(f"/api/admin/reports/{report_id}")
                assert detail.status_code == 200, detail.text
                target = detail.json()["target"]
                assert target["revision_id"] == str(revision_id)
                assert "OLD-DOC-SECRET" in target["excerpt"]
                assert "NEW-DOC-SECRET" not in detail.text
                assert target["live"]["superseded"] is True
                message_detail = await client.get("/api/admin/reports", params={"code": own.json()["public_code"]})
                message_id = message_detail.json()["items"][0]["id"]
                located = await client.get(f"/api/admin/reports/{message_id}")
                assert located.json()["target"]["message_id"] == str(alice_message)
                assert "ALICE-TAIL" not in located.text
                original = await client.post(f"/api/admin/reports/{message_id}/message")
                assert original.status_code == 200 and original.json()["content"].endswith("ALICE-TAIL")
                assert original.headers["cache-control"] == "no-store"
                async with engine.begin() as conn:
                    await conn.execute(update(chat_messages).where(chat_messages.c.id == alice_message).values(
                        content="CHANGED-CONTENT",
                    ))
                changed_message = await client.post(f"/api/admin/reports/{message_id}/message")
                assert changed_message.status_code == 404 and "CHANGED-CONTENT" not in changed_message.text
                use_access_token(client, bob)
                progress = await client.get(f"/api/reports/{reported.json()['public_code']}")
                assert progress.status_code == 200
                assert "OLD-DOC-SECRET" not in progress.text and "NEW-DOC-SECRET" not in progress.text
                blocked = await client.get(
                    f"/api/knowledge-bases/{kb_id}/documents/{document_id}/text",
                    params={"revision_id": str(revision_id)},
                )
                assert blocked.status_code == 404
                assert "OLD-DOC-SECRET" not in blocked.text
                assert (await client.get(f"/api/knowledge-bases/{kb_id}/documents/{document_id}/source")).status_code == 404
                assert (await client.get("/api/admin/reports")).status_code == 403
                assert (await client.post(f"/api/admin/reports/{message_id}/message")).status_code == 403
                use_access_token(client, admin)
                row_id = (await repo.report_by_code(ticket["public_code"]))["id"]
                assert (await client.post(f"/api/admin/reports/{row_id}/accept")).status_code == 200
                assert (await client.post(
                    f"/api/admin/reports/{row_id}/note", json={"note": "SECRET-NOTE"},
                )).status_code == 200
                assert (await client.post(
                    f"/api/admin/reports/{row_id}/reply", json={"feedback": "正在核查"},
                )).status_code == 200
                assert (await client.post(
                    f"/api/admin/reports/{row_id}/resolve", json={"feedback": "", "measure": "已核查"},
                )).status_code == 422
                completed = await asyncio.gather(*[
                    client.post(f"/api/admin/reports/{row_id}/resolve", json={
                        "feedback": "已反馈给访客", "measure": "已核查",
                    }) for _ in range(2)
                ])
                assert sorted(response.status_code for response in completed) == [200, 409]
                async with engine.connect() as conn:
                    resolved_count = (await conn.execute(
                        select(report_events.c.id).where(
                            report_events.c.report_id == row_id, report_events.c.action == "resolved",
                        )
                    )).all()
                assert len(resolved_count) == 1
                del client.headers["Authorization"]
                visitor = await client.post("/api/reports/lookup", json={
                    "public_code": ticket["public_code"], "lookup_code": ticket["lookup_code"],
                })
                assert visitor.status_code == 200
                assert visitor.json()["public_feedback"] == "已反馈给访客"
                assert "SECRET-NOTE" not in visitor.text
        finally:
            await engine.dispose()
            async with control.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await control.dispose()

    asyncio.run(run())
