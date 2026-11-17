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
from melonclaw.core.knowledge import ChunkConfig, KnowledgeDenied, require_role
from melonclaw.core.model_catalog import resolve_knowledge_model_row, resolve_model_row
from melonclaw.database.errors import DatabaseSchemaError
from melonclaw.repository.knowledge import visible_query
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
