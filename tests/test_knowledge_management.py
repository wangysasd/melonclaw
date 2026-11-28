"""知识库权限、用途隔离、固定探针和安全 API 的不依赖数据库回归。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from account_fixtures import authenticated_route_app
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from test_model_configs import make_provider_row, make_row

from melonclaw.api.app import create_app
from melonclaw.api.routes.knowledge import CreateBase, UpdateBase
from melonclaw.core.knowledge import (
    ChunkConfig,
    KnowledgeDenied,
    KnowledgeError,
    is_system_admin,
    knowledge_operation_item,
    require_role,
)
from melonclaw.core.model_catalog import resolve_knowledge_model_row, resolve_model_row
from melonclaw.database.knowledge_schema import EVENT_ACTIONS
from melonclaw.repository.knowledge import (
    EVENT_DETAIL_KEYS,
    suspended_admin_query,
    validate_knowledge_event,
    visible_query,
)
from melonclaw.services.knowledge import KnowledgeService
from melonclaw.services.knowledge_models import test_model_connection as probe_connection
from melonclaw.services.resource_service import ModelConfigPayload, validate_model_payload


@pytest.mark.parametrize("role", ["viewer", "editor", "owner"])
@pytest.mark.parametrize("operation", ["read", "documents", "edit", "members", "delete", "transfer"])
def test_role_matrix(role, operation):
    allowed = (
        operation == "read"
        or (operation == "documents" and role == "editor")
        or role == "owner"
    )
    if allowed:
        require_role(role, operation)
    else:
        with pytest.raises(KnowledgeDenied):
            require_role(role, operation)


def test_system_admin_can_manage_and_delete_but_not_transfer_ownership():
    for operation in ("read", "documents", "edit", "members", "delete"):
        require_role("system_admin", operation)
    with pytest.raises(KnowledgeDenied):
        require_role("system_admin", "transfer")


def test_visible_sql_filters_before_pagination_and_is_parameterized():
    compiled = visible_query("not-a-sql-literal").limit(20).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "not-a-sql-literal" not in sql
    assert {"everyone", "tenant"} <= set(compiled.params.values())
    for required in [
        "actor.tenant_status",
        "owner.tenant_status",
        "actor_tenant.enabled",
        "owner_tenant.enabled",
        "knowledge_bases.status",
        "knowledge_bases.share_scope",
        "actor.tenant_id = owner.tenant_id",
        "membership.role IS NOT NULL",
        "LIMIT",
    ]:
        assert required in sql
    assert "active" in compiled.params.values()
    assert "suspended" not in compiled.params.values()
    moderated = suspended_admin_query("not-a-sql-literal").compile(dialect=postgresql.dialect())
    moderated_sql = str(moderated)
    assert "not-a-sql-literal" not in moderated_sql
    assert {"admin", "system", "suspended"} <= set(moderated.params.values())
    assert "knowledge_documents" not in moderated_sql
    assert "knowledge_chunks" not in moderated_sql


def test_moderation_reasons_are_fixed_codes_and_do_not_echo_document_text():
    assert set(EVENT_DETAIL_KEYS) == set(EVENT_ACTIONS)
    assert is_system_admin("admin", "system")
    assert not is_system_admin("admin", "team")
    assert not is_system_admin("owner", "system")
    validate_knowledge_event("base_suspended", {"reason": "report_review"})
    validate_knowledge_event("base_deleted", {})
    validate_knowledge_event("base_deleted", {"reason": "confirmed_violation"})
    for values in (
        {},
        {"reason": "自由文本"},
        {"reason": "report_review", "content": "文档正文"},
    ):
        with pytest.raises(KnowledgeError):
            validate_knowledge_event("base_suspended", values)
    with pytest.raises(KnowledgeError):
        validate_knowledge_event("base_deleted", {"reason": "把正文写进原因"})
    item = knowledge_operation_item(
        {
            "id": "event",
            "kb_id": "kb",
            "action": "base_suspended",
            "actor_user_id": "admin",
            "user_name_zh": "管理员",
            "target_id": "kb",
            "created_at": "2026-11-26T00:00:00Z",
            "safe_details": {"reason": "report_review", "content": "不应出现的文档正文"},
        },
        kb_name="库A",
    )
    assert item["detail"] == "接到举报，暂停核查"
    assert item["action_label"] == "停用"
    assert "不应出现" not in str(item)
    assert "content" not in item


def test_moderation_routes_use_the_verified_user_and_reject_free_text_reasons():
    async def run():
        from melonclaw.api.routes.knowledge import DisposeRequest, RestoreRequest, SuspendRequest
        from melonclaw.core.knowledge import DELETE_REASONS, RESTORE_REASONS, SUSPEND_REASONS

        assert set(SuspendRequest.model_fields["reason"].annotation.__args__) == set(SUSPEND_REASONS)
        assert set(RestoreRequest.model_fields["reason"].annotation.__args__) == set(RESTORE_REASONS)
        assert set(DisposeRequest.model_fields["reason"].annotation.__args__) == set(DELETE_REASONS)
        repo = SimpleNamespace(
            knowledge_suspend=AsyncMock(return_value={"id": "kb", "status": "suspended", "version": 2}),
            knowledge_moderation_list=AsyncMock(side_effect=KnowledgeDenied("知识库不存在或无权访问。")),
        )
        app = authenticated_route_app(create_app(), "verified-user")
        app.state.chat = SimpleNamespace(
            runtime=SimpleNamespace(require_ready=lambda: repo, settings=object())
        )
        kb_id = str(uuid4())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            rejected = await client.post(
                f"/api/knowledge-bases/{kb_id}/suspend",
                json={"version": 1, "reason": "自由输入的文档正文"},
            )
            assert rejected.status_code == 422
            assert "文档" not in rejected.text
            accepted = await client.post(
                f"/api/knowledge-bases/{kb_id}/suspend",
                json={"version": 3, "reason": "report_review"},
            )
            assert accepted.status_code == 200
            args = repo.knowledge_suspend.await_args.args
            assert args[0] == "verified-user"
            assert str(args[1]) == kb_id
            assert args[2:] == (3, "report_review")
            denied = await client.get("/api/knowledge-moderation/bases")
            assert denied.status_code == 404
            history = await client.get(f"/api/knowledge-bases/{kb_id}/operations")
            assert history.status_code == 403

    asyncio.run(run())


def test_chunk_and_mutation_schemas_reject_unbounded_or_legacy_fields():
    assert ChunkConfig().model_dump() == {"chunk_size": 800, "chunk_overlap": 120}
    for values in [
        {"chunk_size": 100, "chunk_overlap": 100},
        {"chunk_size": 8000, "chunk_overlap": 120},
        {"chunk_size": 800, "chunk_overlap": 200},
        {"chunk_size": True},
        {"revision_state": "ready"},
    ]:
        with pytest.raises(ValidationError):
            ChunkConfig(**values)
    with pytest.raises(ValidationError):
        CreateBase(name="test", tenant_id="other")
    with pytest.raises(ValidationError):
        UpdateBase(version=1, embedding_model_id=str(uuid4()))
    with pytest.raises(ValidationError):
        UpdateBase(version=1, name=None)


def test_knowledge_models_never_consult_personal_or_environment_keys(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "environment-test-only")
    model = make_row(purpose="document_parser", model_name="parser", context_window=None)
    provider = make_provider_row(api_key="database-shared-test", api_key_env="TEST_API_KEY")
    assert resolve_knowledge_model_row(model, provider).api_key == "database-shared-test"
    assert "database-shared-test" not in repr(resolve_knowledge_model_row(model, provider))
    with pytest.raises(ValueError, match="数据库共享"):
        resolve_knowledge_model_row(model, {**provider, "api_key": None})
    with pytest.raises(ValueError):
        resolve_knowledge_model_row({**model, "scope": "user"}, provider)
    with pytest.raises(ValueError, match="聊天"):
        resolve_model_row(model, provider)
    with pytest.raises(ValueError):
        validate_model_payload(
            ModelConfigPayload(
                "embed",
                "provider",
                "global",
                "embed",
                "BAAI/bge-m3",
                context_window=None,
                purpose="embedding",
            )
        )
    with pytest.raises(ValueError):
        validate_model_payload(
            ModelConfigPayload(
                "embed",
                "provider",
                "user",
                "embed",
                "BAAI/bge-m3",
                context_window=None,
                purpose="embedding",
            )
        )


def test_fixed_probe_payload_and_validation(monkeypatch):
    import json

    import melonclaw.services.knowledge_models as module

    requests = []

    def capture(req):
        requests.append(json.loads(req.content))
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "2026"}}]}
        )

    monkeypatch.setattr(
        module, "PublicDownloadTransport", lambda **kw: httpx.MockTransport(capture)
    )
    row = make_row(purpose="document_parser", model_name="parser", context_window=None)
    provider = make_provider_row(api_key="shared-probe-test")
    storage = SimpleNamespace(
        get_user_context=AsyncMock(return_value=SimpleNamespace(tenant_role="admin")),
        get_model_with_provider=AsyncMock(return_value=(row, provider)),
    )

    async def run():
        assert (await probe_connection(storage, "admin", "parser"))["ok"]
        assert requests[0]["messages"][0]["content"][1]["image_url"]["url"] == module.TEST_IMAGE
        storage.get_model_with_provider.return_value = ({**row, "purpose": "embedding"}, provider)
        with pytest.raises(ValueError, match="内部服务"):
            await probe_connection(storage, "admin", "embed")
        assert len(requests) == 1

    asyncio.run(run())


def test_knowledge_routes_use_cookie_identity_and_do_not_echo_database_failure():
    async def run():
        repo = SimpleNamespace(
            get_user_context=AsyncMock(return_value=object()),
            fixed_embedding_model_id=AsyncMock(return_value=uuid4()),
            knowledge_create=AsyncMock(return_value={"id": str(uuid4())}),
            knowledge_detail=AsyncMock(side_effect=RuntimeError("driver secret payload")),
        )
        app = authenticated_route_app(create_app(), "verified-user")
        app.state.chat = SimpleNamespace(runtime=SimpleNamespace(require_ready=lambda: repo, settings=object()))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post("/api/knowledge-bases", json={"name": "  Test  "})
            assert response.status_code == 201
            assert repo.knowledge_create.call_args.args[0] == "verified-user"
            assert repo.knowledge_create.call_args.args[1]["name"] == "Test"
            bad = await client.post(
                "/api/knowledge-bases", json={"name": "Test", "owner_user_id": "someone"}
            )
            assert bad.status_code == 422
            response = await client.get("/api/knowledge-bases/" + str(uuid4()))
            assert response.status_code == 503
            assert "secret" not in response.text
            repo.knowledge_detail.side_effect = None
            repo.knowledge_detail.return_value = {"id": "kb", "name": "Test"}
            repo.knowledge_published_document_count = AsyncMock(return_value=4)
            ok = await client.get("/api/knowledge-bases/" + str(uuid4()))
            assert ok.status_code == 200
            body = ok.json()
            assert body["published_document_count"] == 4
            assert body["name"] == "Test"
            assert "embedding_model_id" not in body
            assert repo.knowledge_detail.await_args.args[0] == "verified-user"
        service = KnowledgeService(repo, object())
        with pytest.raises(ValueError):
            await service.create("verified-user", {"name": " "})

    asyncio.run(run())


def test_model_schema_verifier_checks_nullability_and_default_index(monkeypatch):
    from melonclaw.database import model_ddl
    from melonclaw.database.errors import DatabaseSchemaError

    class Inspector:
        def get_columns(self, name):
            return [
                {"name": "purpose", "nullable": False},
                {"name": "context_window", "nullable": True},
            ]

        def get_check_constraints(self, name):
            return [
                {"name": n}
                for n in [
                    "ck_model_configs_purpose",
                    "ck_model_configs_context_window",
                    "ck_model_configs_embedding",
                ]
            ]

        def get_indexes(self, name):
            return [
                {
                    "name": "ux_model_configs_is_default",
                    "column_names": ["purpose"],
                    "unique": True,
                    "dialect_options": {
                        "postgresql_where": "is_default IS TRUE AND scope='global'"
                    },
                },
                {
                    "name": "ux_model_configs_user_default",
                    "column_names": ["created_by", "purpose"],
                    "unique": True,
                    "dialect_options": {"postgresql_where": "is_default IS TRUE AND scope='user'"},
                },
            ]

    inspector = Inspector()
    monkeypatch.setattr(model_ddl, "inspect", lambda _: inspector)

    class Connection:
        async def run_sync(self, fn):
            return fn(None)

    async def run():
        await model_ddl.verify_model_purpose(Connection())
        inspector.get_indexes = lambda name: []
        with pytest.raises(DatabaseSchemaError):
            await model_ddl.verify_model_purpose(Connection())

    asyncio.run(run())


def test_audit_rejects_request_payload_and_sensitive_or_unbounded_details():
    from melonclaw.repository.knowledge import KnowledgeRepositoryMixin

    repo = KnowledgeRepositoryMixin()
    conn = SimpleNamespace(execute=AsyncMock())

    async def run():
        for details in [
            {"body": "private"},
            {"fields": ["api_key"], "version": 1},
            {"role": "owner"},
            {"new_owner": "a" * 65},
        ]:
            action = (
                "member_granted"
                if "role" in details
                else "ownership_transferred"
                if "new_owner" in details
                else "base_updated"
            )
            with pytest.raises(ValueError):
                await repo._knowledge_event(conn, uuid4(), "actor", action, **details)
        conn.execute.assert_not_awaited()

    asyncio.run(run())


def test_knowledge_remote_model_list_uses_only_shared_key():
    from test_model_configs import FakeStorage, make_service

    async def run():
        storage = FakeStorage()
        storage.providers["my-gateway"] = make_provider_row(api_key="database-test-only")
        storage.user_keys[("my-gateway", "admin-1")] = "personal-test-only"
        service = make_service(storage)
        service._request_remote_models = AsyncMock(return_value={"items": []})
        await service.fetch_remote_models("admin-1", "my-gateway", purpose="document_parser")
        assert (
            service._request_remote_models.call_args.args[1]["Authorization"]
            == "Bearer database-test-only"
        )
        assert service._request_remote_models.call_args.kwargs == {"knowledge": True}
        storage.providers["my-gateway"]["api_key"] = None
        with pytest.raises(ValueError, match="共享 Key"):
            await service.fetch_remote_models("admin-1", "my-gateway", purpose="document_parser")

    asyncio.run(run())


def test_operation_item_shows_scope_change_and_only_recorded_reasons():
    from melonclaw.core.knowledge import knowledge_operation_item

    share = knowledge_operation_item(
        {
            "id": "1",
            "created_at": "t",
            "actor_user_id": "owner",
            "user_name_zh": "甲",
            "kb_id": "kb",
            "action": "share_scope_changed",
            "target_id": "kb",
            "safe_details": {"previous": "tenant", "share_scope": "everyone"},
        },
        kb_name="甲库",
    )
    assert share["detail"] == "同租户 → 所有用户"
    assert share["actor_label"] == "甲（owner）"
    assert share["kb_name"] == "甲库"
    missing = knowledge_operation_item(
        {
            "id": "2",
            "created_at": "t",
            "actor_user_id": "owner",
            "user_name_zh": "甲",
            "kb_id": "kb",
            "action": "base_deleted",
            "target_id": "kb",
            "safe_details": {},
        },
        kb_name="甲库",
    )
    assert missing["detail"] == "未记录"
    recorded = knowledge_operation_item(
        {
            "id": "3",
            "created_at": "t",
            "actor_user_id": None,
            "kb_id": "kb",
            "action": "base_suspended",
            "target_id": "kb",
            "safe_details": {"reason": "report_review"},
        },
        kb_name="甲库",
    )
    assert recorded["actor_label"] == "系统"
    assert recorded["action_label"] == "停用"
    assert recorded["detail"] == "接到举报，暂停核查"


def test_operation_history_route_rejects_non_admin_before_reading():
    repo = SimpleNamespace(knowledge_operation_history=AsyncMock(return_value={"items": []}))

    async def run():
        denied = authenticated_route_app(create_app(), "owner")
        denied.state.chat = SimpleNamespace(runtime=SimpleNamespace(require_ready=lambda: repo))
        allowed = authenticated_route_app(create_app(), "admin")
        allowed.state.chat = SimpleNamespace(runtime=SimpleNamespace(require_ready=lambda: repo))
        kb_id = str(uuid4())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(denied), base_url="http://test") as client:
            response = await client.get(f"/api/knowledge-bases/{kb_id}/operations")
            assert response.status_code == 403
            assert "只有系统管理员" in response.text
        repo.knowledge_operation_history.assert_not_called()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(allowed), base_url="http://test") as client:
            response = await client.get(f"/api/knowledge-bases/{kb_id}/operations")
            assert response.status_code == 200
        assert repo.knowledge_operation_history.await_args.args[0] == "admin"
        assert str(repo.knowledge_operation_history.await_args.args[1]) == kb_id

    asyncio.run(run())
