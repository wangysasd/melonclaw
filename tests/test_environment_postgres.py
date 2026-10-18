"""隔离 PostgreSQL schema 验证环境变量、审批与身份读取；不调用外部服务。"""

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import (
    environment_operations,
    environment_variables,
    metadata,
    tenants,
    users,
)
from melonclaw.database.updates.environment import upgrade
from melonclaw.repository import BusinessRepository
from melonclaw.repository.environment import EnvironmentConflict
from melonclaw.repository.mappers import _now
from melonclaw.services.accounts import AccountService, token_digest
from melonclaw.services.environment import EnvironmentError, EnvironmentNotFound, EnvironmentService
from melonclaw.services.environment_chat import ChatEnvironmentService, parse_environment_chat


@pytest.mark.skipif(
    not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离 PostgreSQL 测试连接"
)
def test_environment_resources_approval_and_authentication():
    async def run():
        schema = "test_environment_" + uuid4().hex
        engine = create_async_engine(
            normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
            connect_args={"server_settings": {"search_path": schema}},
        )
        statements = []

        def count(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                await conn.run_sync(
                    lambda sync: metadata.create_all(
                        sync,
                        tables=[
                            table
                            for table in metadata.sorted_tables
                            if table.name not in {"environment_variables", "environment_operations"}
                        ],
                    )
                )
                # 新增升级步骤可重入，既有数据保持。
                await upgrade(conn)
                await upgrade(conn)
                await conn.execute(
                    insert(tenants).values(
                        tenant_id="test", tenant_name_zh="测试", created_at=_now()
                    )
                )
                for identifier in ("admin", "alice", "bob", "quota", "bytes", "role", "sort"):
                    await conn.execute(
                        insert(users).values(
                            user_id=identifier,
                            user_name_zh=identifier,
                            tenant_id="test",
                            tenant_role="admin" if identifier == "admin" else "member",
                            created_at=_now(),
                        )
                    )
            storage = BusinessRepository(SimpleNamespace(engine=engine))
            runtime = SimpleNamespace(require_ready=lambda: storage)
            management = EnvironmentService(runtime)
            provider = ChatEnvironmentService(SimpleNamespace(runtime=runtime))

            # Enabled rows precede disabled rows, names ascend within each group.
            for name, enabled in (("Z_ON", True), ("A_OFF", False), ("A_ON", True), ("Z_OFF", False)):
                await management.save("sort", name=name, fields={"value": "", "enabled": enabled})
            listed = await management.list("sort")
            assert [item["name"] for item in listed["items"]] == [
                "A_ON", "Z_ON", "A_OFF", "Z_OFF",
            ]

            # UTF-8 byte limits accept exactly 8192 bytes and reject the next byte.
            utf8_limit = await management.save(
                "bytes", name="UTF8_LIMIT", fields={"value": "é" * 4096}
            )
            with pytest.raises(EnvironmentError):
                await management.save("bytes", name="UTF8_OVER", fields={"value": "é" * 4097})
            assert not any(
                row["name"] == "UTF8_OVER" for row in await storage.list_environment("bytes")
            )
            await management.save(
                "bytes", identifier=utf8_limit["id"], expected_version=1, delete=True
            )

            # Enabled environment payload accounting accepts exactly 64 KiB, then
            # rejects an update without replacing the stored value.
            for index in range(7):
                await management.save(
                    "bytes", name=f"B{index}", fields={"value": "v" * 8192}
                )
            last = await management.save(
                "bytes", name="B7", fields={"value": "v" * 8160}
            )
            with pytest.raises(EnvironmentConflict):
                await management.save(
                    "bytes",
                    identifier=last["id"],
                    expected_version=1,
                    fields={"value": "x" * 8161},
                )
            boundary_row = next(
                row for row in await storage.list_environment("bytes") if row["name"] == "B7"
            )
            assert boundary_row["value"] == "v" * 8160 and boundary_row["version"] == 1

            # Seed 198 rows so service writes exercise the 200-item boundary
            # without spending 198 setup transactions.
            async with engine.begin() as conn:
                await conn.execute(
                    insert(environment_variables),
                    [
                        {
                            "id": uuid4(),
                            "name": f"Q{index:03d}",
                            "description": "",
                            "scope": "user",
                            "owner_user_id": "quota",
                            "created_by": "quota",
                            "value": "",
                            "enabled": True,
                            "version": 1,
                            "created_at": _now(),
                            "updated_at": _now(),
                        }
                        for index in range(198)
                    ],
                )
            await management.save("quota", name="Q198", fields={"value": ""})
            await management.save("quota", name="Q199", fields={"value": ""})
            with pytest.raises(EnvironmentConflict):
                await management.save("quota", name="Q200", fields={"value": ""})
            assert len(await storage.list_environment("quota")) == 200
            assert not any(
                row["name"] == "Q200" for row in await storage.list_environment("quota")
            )

            shared = await management.save("admin", name="DEMO", fields={"value": "shared-fixture"})
            personal = await management.save("alice", name="DEMO", fields={"value": ""})
            assert await storage.effective_environment("alice") == {"DEMO": ""}
            assert await storage.effective_environment("bob") == {"DEMO": "shared-fixture"}
            assert "shared-fixture" not in str(await management.list("alice"))
            assert not (await management.list("bob"))["items"]
            with pytest.raises(EnvironmentNotFound):
                await management.save(
                    "bob", identifier=personal["id"], expected_version=1, fields={"enabled": False}
                )
            stopped = await management.save(
                "alice", identifier=personal["id"], expected_version=1, fields={"enabled": False}
            )
            assert stopped["version"] == 2 and await storage.effective_environment("alice") == {
                "DEMO": "shared-fixture"
            }
            with pytest.raises(EnvironmentConflict):
                await management.save(
                    "alice",
                    identifier=personal["id"],
                    expected_version=1,
                    fields={"value": "stale"},
                )
            await management.save(
                "alice", identifier=personal["id"], expected_version=2, delete=True
            )
            await management.save(
                "admin", identifier=shared["id"], expected_version=1, fields={"enabled": False}
            )
            assert await storage.effective_environment("alice") == {}
            assert (await storage.resource_versions.read("bob"))[0] > 0

            for user_id in ("admin", "alice"):
                conversation = await storage.create_conversation(user_id, None)
                context = SimpleNamespace(
                    user_id=user_id,
                    tenant_id="test",
                    conversation_id=conversation["id"],
                    project_id="",
                    user_message_id=str(uuid4()),
                    request_id=str(uuid4()),
                )
                user = await storage.get_user_context(user_id)

                async def stage(text, request_id=None):
                    if request_id:
                        context.request_id = request_id
                    clean, draft = parse_environment_chat(
                        text,
                        user_id=user_id,
                        conversation_id=context.conversation_id,
                        request_id=context.request_id,
                    )
                    assert "chat-fixture" not in clean
                    await provider.stage(draft, user, context.conversation_id, context.request_id)
                    prepared = await provider.prepare(context, draft["id"])
                    assert prepared["status"] == "prepared"
                    return draft, prepared["change"]

                draft, manifest = await stage("设置环境变量 CHAT_KEY=chat-fixture")
                assert manifest["scope"] == ("global" if user_id == "admin" else "user")
                assert "chat-fixture" not in str(manifest)
                wrong_request = SimpleNamespace(**{**vars(context), "request_id": "other"})
                assert (await provider.confirm(wrong_request, manifest))["status"] == "error"
                wrong_conversation = await storage.create_conversation(user_id, None)
                wrong_conversation_context = SimpleNamespace(
                    **{**vars(context), "conversation_id": wrong_conversation["id"]}
                )
                assert (
                    await provider.confirm(wrong_conversation_context, manifest)
                )["status"] == "error"
                other_user_id = "alice" if user_id == "admin" else "bob"
                other_user_conversation = await storage.create_conversation(other_user_id, None)
                wrong_user_context = SimpleNamespace(
                    **{
                        **vars(context),
                        "user_id": other_user_id,
                        "conversation_id": other_user_conversation["id"],
                    }
                )
                assert (await provider.confirm(wrong_user_context, manifest))["status"] == "error"
                assert (await provider.confirm(context, {**manifest, "name": "FORGED"}))[
                    "status"
                ] == "error"
                replies = await asyncio.gather(
                    provider.confirm(context, manifest), provider.confirm(context, manifest)
                )
                assert all(item["status"] == "saved" for item in replies)
                assert replies[0]["id"] == replies[1]["id"]
                committed = await storage.environment_operation(
                    draft["id"], user_id, context.conversation_id
                )
                assert committed["payload"] is None and committed["status"] == "committed"
                assert (
                    await provider.prepare(
                        SimpleNamespace(**{**vars(context), "request_id": "other"}), draft["id"]
                    )
                )["status"] == "error"
                # 页面编辑发生在审批等待期间，旧审批不能覆盖。
                _, stale = await stage("更新环境变量 CHAT_KEY=chat-fixture-new", str(uuid4()))
                await management.save(
                    user_id,
                    identifier=replies[0]["id"],
                    expected_version=1,
                    fields={"description": "changed"},
                )
                assert (await provider.confirm(context, stale))["status"] == "error"
                # 拒绝、过期后真实值清空且引用永久不能重放。
                for status in ("rejected", "expired"):
                    draft, manifest = await stage(
                        "设置环境变量 TEMP_KEY=chat-fixture", str(uuid4())
                    )
                    if status == "rejected":
                        await storage.reject_environment(
                            draft["id"], user_id, context.conversation_id
                        )
                    else:
                        async with engine.begin() as conn:
                            await conn.execute(
                                update(environment_operations)
                                .where(environment_operations.c.request_id == context.request_id)
                                .values(expires_at=_now() - timedelta(seconds=1))
                            )
                        await storage.cleanup_environment_operations()
                    assert (await provider.confirm(context, manifest))["status"] == "error"
                    async with engine.connect() as conn:
                        row = (
                            (
                                await conn.execute(
                                    select(environment_operations).where(
                                        environment_operations.c.request_id == context.request_id
                                    )
                                )
                            )
                            .mappings()
                            .one()
                        )
                    assert row["status"] == status and row["payload"] is None

                if user_id == "alice":
                    # A prepared personal-scope approval cannot cross a role change.
                    role_draft, role_manifest = await stage(
                        "设置环境变量 ROLE_KEY=chat-fixture", str(uuid4())
                    )
                    async with engine.begin() as conn:
                        await conn.execute(
                            update(users)
                            .where(users.c.user_id == user_id)
                            .values(tenant_role="admin")
                        )
                    assert (await provider.confirm(context, role_manifest))["status"] == "error"
                    async with engine.begin() as conn:
                        await conn.execute(
                            update(users)
                            .where(users.c.user_id == user_id)
                            .values(tenant_role="member")
                        )
                    await storage.reject_environment(
                        role_draft["id"], user_id, context.conversation_id
                    )

                    # After 30 days only the response is cleared; the committed
                    # idempotency tombstone still prevents a second write.
                    receipt_draft, receipt_manifest = await stage(
                        "设置环境变量 RECEIPT_KEY=chat-fixture", str(uuid4())
                    )
                    receipt = await provider.confirm(context, receipt_manifest)
                    assert receipt["status"] == "saved"
                    async with engine.begin() as conn:
                        await conn.execute(
                            update(environment_operations)
                            .where(environment_operations.c.id == receipt_draft["id"])
                            .values(created_at=_now() - timedelta(days=31))
                        )
                    await storage.cleanup_environment_operations()
                    tombstone = await storage.environment_operation(
                        receipt_draft["id"], user_id, context.conversation_id
                    )
                    assert (
                        tombstone["status"] == "committed"
                        and tombstone["payload"] is None
                        and tombstone["response"] is None
                    )
                    assert (await provider.confirm(context, receipt_manifest))["status"] == "error"
                    assert next(
                        row for row in await storage.list_environment(user_id)
                        if row["name"] == "RECEIPT_KEY"
                    )["version"] == 1
            # HTTP 验证错误不回显 body，客户端不能指定共享 scope/owner。
            import httpx

            from melonclaw.api.app import create_app

            app = create_app()
            app.state.chat = SimpleNamespace(storage=storage, runtime=runtime)
            await storage.create_auth_session(token_digest("http-fixture"), "bob")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url="http://test",
                cookies={"melonclaw_session": "http-fixture"},
            ) as client:
                invalid = await client.post(
                    "/api/environment",
                    json={
                        "user_id": "bob",
                        "name": "TOKEN",
                        "value": "hidden-fixture",
                        "scope": "global",
                    },
                )
                assert invalid.status_code == 422 and "hidden-fixture" not in invalid.text
                saved = await client.post(
                    "/api/environment",
                    json={"user_id": "bob", "name": "TOKEN", "value": "hidden-fixture"},
                )
                assert saved.status_code == 201 and "hidden-fixture" not in saved.text
                listed = await client.get("/api/environment", params={"user_id": "bob"})
                assert listed.status_code == 200 and "hidden-fixture" not in listed.text
                assert (
                    await client.get("/api/environment", params={"user_id": "alice"})
                ).status_code == 409
            from uuid import UUID

            from melonclaw.repository import ConversationNotFoundError
            from melonclaw.services.conversations import ConversationService
            from melonclaw.services.preparation import prepare_context

            project = await storage.create_project("bob", "project")
            conversation = await storage.create_conversation("bob", UUID(project["id"]))
            user = await storage.get_user_context("bob")
            statements.clear()
            preparation = await prepare_context(
                ConversationService(runtime), UUID(conversation["id"]), "bob", user, storage=storage
            )
            assert (
                len(statements) == 1
                and preparation.project["workdir_path"] == project["workdir_path"]
            )
            await storage.update_project(UUID(project["id"]), "bob", delete=True)
            with pytest.raises(ConversationNotFoundError):
                await prepare_context(
                    ConversationService(runtime),
                    UUID(conversation["id"]),
                    "bob",
                    user,
                    storage=storage,
                )
            # 认证只一次 SELECT；原登录身份失效也拒绝当前模拟身份。
            await storage.create_auth_session(token_digest("fixture-session"), "alice")
            await storage.switch_auth_session(token_digest("fixture-session"), "bob")
            statements.clear()
            assert (await AccountService(storage).session("fixture-session")).user_id == "bob"
            assert len(statements) == 1
            async with engine.begin() as conn:
                await conn.execute(
                    update(users).where(users.c.user_id == "alice").values(tenant_status="deleted")
                )
            assert await AccountService(storage).session("fixture-session") is None
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    asyncio.run(run())
