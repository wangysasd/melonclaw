"""开发侧固定契约和用户侧不可配置边界。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_model_configs import (
    FakeStorage,
    make_provider_payload,
    make_provider_row,
    make_row,
    make_service,
)

from melonclaw.api.routes.knowledge import CreateBase, public_result
from melonclaw.core import knowledge_embedding as module
from melonclaw.services.knowledge import KnowledgeService
from melonclaw.services.resource_service import ModelConfigPayload, ResourcePermissionError


def embedding_settings(**overrides):
    values = dict(
        knowledge_embedding_api_key="developer-test-only",
        knowledge_embedding_url="https://api.siliconflow.cn/v1",
        knowledge_embedding_model_name="BAAI/bge-m3",
        knowledge_embedding_dimensions=1024,
    )
    return SimpleNamespace(**(values | overrides))


def test_fixed_factory_matches_demo_and_disables_environment_fallback(monkeypatch):
    arguments = {}
    monkeypatch.setattr(module, "OpenAIEmbeddings", lambda **kw: arguments.update(kw) or object())
    clients = []

    def client(**kw):
        clients.append(kw)
        return object()

    monkeypatch.setattr(module.httpx, "Client", client)
    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    monkeypatch.setenv("OPENAI_API_KEY", "unwanted-test-only")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unwanted.example")
    with pytest.raises(ValueError, match="开发人员"):
        module.build_knowledge_embeddings(embedding_settings(knowledge_embedding_api_key=""))
    module.build_knowledge_embeddings(embedding_settings())
    assert arguments["model"] == "BAAI/bge-m3"
    assert arguments["base_url"] == "https://api.siliconflow.cn/v1"
    assert arguments["api_key"] == "developer-test-only"
    assert embedding_settings().knowledge_embedding_dimensions == 1024
    assert arguments["model_kwargs"] == {"encoding_format": "float"}
    assert arguments["chunk_size"] == 16 and arguments["request_timeout"] == 60
    assert arguments["max_retries"] == 0 and arguments["check_embedding_ctx_length"] is False
    assert len(clients) == 2 and all(
        c == {"trust_env": False, "follow_redirects": False} for c in clients
    )


def test_create_binds_internal_reference_and_hides_it_from_api():
    with pytest.raises(ValidationError):
        CreateBase(name="test", embedding_model_id=str(module.EMBEDDING_MODEL_ID))
    repo = SimpleNamespace(
        get_user_context=AsyncMock(return_value=object()),
        fixed_embedding_model_id=AsyncMock(return_value=module.EMBEDDING_MODEL_ID),
        knowledge_create=AsyncMock(return_value={"id": "base"}),
    )

    async def run():
        await KnowledgeService(repo, embedding_settings()).create("user", {"name": "test"})
        assert (
            repo.knowledge_create.call_args.args[1]["embedding_model_id"]
            == module.EMBEDDING_MODEL_ID
        )
        with pytest.raises(ValueError):
            await KnowledgeService(repo, embedding_settings()).create(
                "user", {"name": "test", "embedding_model_id": None}
            )

    asyncio.run(run())
    assert public_result({"items": [{"name": "test", "embedding_model_id": "private"}]}) == {
        "items": [{"name": "test"}]
    }


def test_admin_cannot_configure_internal_embedding_or_its_provider():
    async def run():
        storage = FakeStorage()
        storage.providers[module.EMBEDDING_PROVIDER_KEY] = make_provider_row(
            provider_key=module.EMBEDDING_PROVIDER_KEY
        )
        storage.rows["embed"] = make_row(purpose="embedding")
        service = make_service(storage)
        operations = [
            service.list_models("admin-1", purpose="embedding"),
            service.create_model(
                "admin-1",
                ModelConfigPayload(
                    "embed",
                    "provider",
                    "global",
                    "embed",
                    "BAAI/bge-m3",
                    context_window=None,
                    purpose="embedding",
                ),
            ),
            service.update_model("admin-1", "embed", enabled=False),
            service.update_model("admin-1", "embed", is_default=True),
            service.delete_model("admin-1", "embed"),
            service.create_provider(
                "admin-1", make_provider_payload(provider_key=module.EMBEDDING_PROVIDER_KEY)
            ),
            service.update_provider("admin-1", module.EMBEDDING_PROVIDER_KEY, api_key="test-only"),
            service.delete_provider("admin-1", module.EMBEDDING_PROVIDER_KEY),
            service.set_user_provider_key("admin-1", module.EMBEDDING_PROVIDER_KEY, "test-only"),
            service.fetch_remote_models("admin-1", module.EMBEDDING_PROVIDER_KEY),
        ]
        for operation in operations:
            with pytest.raises(ResourcePermissionError):
                await operation
        assert (await service.list_providers("admin-1"))["items"] == []

    asyncio.run(run())


def test_internal_reference_is_stable_and_rejects_semantic_conflicts():
    from melonclaw.repository.knowledge_embedding import KnowledgeEmbeddingRepositoryMixin

    model = make_row(
        id=module.EMBEDDING_MODEL_ID,
        purpose="embedding",
        model_name=embedding_settings().knowledge_embedding_model_name,
        provider_key=module.EMBEDDING_PROVIDER_KEY,
        source_type="system",
        context_window=None,
    )
    provider = make_provider_row(
        provider_key=module.EMBEDDING_PROVIDER_KEY,
        source_type="system",
        base_url=embedding_settings().knowledge_embedding_url,
        api_key=None,
    )
    verify = KnowledgeEmbeddingRepositoryMixin._verify_fixed_embedding_reference
    verify(model, provider, embedding_settings())
    for bad_model, bad_provider in [
        ({**model, "model_name": "other"}, provider),
        (model, {**provider, "base_url": "https://other.example"}),
        (model, {**provider, "api_key": "forbidden-test-only"}),
    ]:
        with pytest.raises(ValueError):
            verify(bad_model, bad_provider, embedding_settings())

    async def run():
        repo = KnowledgeEmbeddingRepositoryMixin()
        repo.get_model_with_provider = AsyncMock(return_value=(model, provider))
        repo._install_fixed_embedding_reference = AsyncMock(return_value=module.EMBEDDING_MODEL_ID)
        assert (
            await repo.fixed_embedding_model_id(embedding_settings()) == module.EMBEDDING_MODEL_ID
        )
        repo._install_fixed_embedding_reference.assert_not_awaited()
        repo.get_model_with_provider.return_value = None
        assert (
            await repo.fixed_embedding_model_id(embedding_settings()) == module.EMBEDDING_MODEL_ID
        )
        repo._install_fixed_embedding_reference.assert_awaited_once()

    asyncio.run(run())


def test_developer_key_is_not_in_settings_repr(tmp_path):
    from melonclaw.core.config import Settings

    settings = Settings(
        workspace_root=tmp_path,
        data_root=tmp_path,
        knowledge_embedding_api_key="private-test-marker",
    )
    assert "private-test-marker" not in repr(settings)


def test_four_values_come_from_config_environment(monkeypatch, tmp_path):
    from melonclaw.core.config import load_settings

    monkeypatch.setenv("MELONCLAW_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("MELONCLAW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_API_KEY", "environment-test-key")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_URL", "https://configured.example/v1")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_MODEL_NAME", "configured-model")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_DIMENSIONS", "1024")
    settings = load_settings()
    assert settings.knowledge_embedding_url == "https://configured.example/v1"
    assert settings.knowledge_embedding_model_name == "configured-model"
    assert settings.knowledge_embedding_dimensions == 1024
    assert settings.knowledge_embedding_api_key == "environment-test-key"
    arguments = {}
    monkeypatch.setattr(module, "OpenAIEmbeddings", lambda **kw: arguments.update(kw) or object())
    monkeypatch.setattr(module.httpx, "Client", lambda **kw: object())
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: object())
    module.build_knowledge_embeddings(settings)
    assert (
        arguments["model"] == "configured-model"
        and arguments["base_url"] == "https://configured.example/v1"
    )
    assert "dimensions" not in arguments
    for name in [
        "KNOWLEDGE_EMBEDDING_URL",
        "KNOWLEDGE_EMBEDDING_MODEL_NAME",
        "KNOWLEDGE_EMBEDDING_DIMENSIONS",
    ]:
        monkeypatch.delenv(name)
    missing = load_settings()
    assert (
        missing.knowledge_embedding_url == ""
        and missing.knowledge_embedding_model_name == ""
        and missing.knowledge_embedding_dimensions == 0
    )
    with pytest.raises(ValueError):
        module.build_knowledge_embeddings(missing)
    with pytest.raises(ValueError):
        module.validate_embedding_settings(embedding_settings(knowledge_embedding_dimensions=768))
