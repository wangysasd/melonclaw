"""供应商配置透传、凭据优先级和输入边界。"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_model_configs import make_provider_row, make_row

from melonclaw.core.chat_model import build_chat_model
from melonclaw.core.model_catalog import resolve_model_row
from melonclaw.repository.resources import ResourceRepositoryMixin
from melonclaw.services.provider_config import ModelConfigError, validate_provider_advanced
from melonclaw.services.resource_service import provider_public_dict


def test_advanced_settings_reach_model_without_public_header_values():
    provider = make_provider_row(
        request_headers={"X-Client": "test-client"},
        extra_config={"thinking": {"type": "enabled"}},
    )
    resolved = resolve_model_row(make_row(), provider)
    model = build_chat_model(resolved)
    assert model.default_headers == provider["request_headers"]
    assert model.extra_body == provider["extra_config"]
    assert "test-client" not in str(model.to_json())
    assert "test-client" not in repr(model)
    public = provider_public_dict(provider)
    assert public["has_request_headers"] is True
    assert "test-client" not in str(public)
    assert "test-client" not in repr(resolved)


@pytest.mark.parametrize("env,headers,extra", [
    ("DATABASE_URL", {}, {}),
    ("", {"Authorization": "value"}, {}),
    ("", {"X-Client": "first\r\nsecond"}, {}),
    ("", {"X-Client": "a", "x-client": "b"}, {}),
    ("", {}, {"messages": []}),
    ("", {}, {"tools": []}),
    ("", {}, {"nested": {"api_key": "test"}}),
    ("", {}, {"value": float("nan")}),
])
def test_invalid_advanced_settings(env, headers, extra):
    with pytest.raises(ModelConfigError):
        validate_provider_advanced(env, headers, extra)


def test_personal_then_shared_then_env_key(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "env-test")
    storage = ResourceRepositoryMixin()
    storage.get_user_provider_key = AsyncMock(return_value=None)
    row = make_provider_row(api_key=None, api_key_env="TEST_PROVIDER_API_KEY")
    assert asyncio.run(storage.effective_provider_api_key(row, "member")) == "env-test"
    assert provider_public_dict(row)["has_api_key"] is True
    row["api_key"] = "shared-test"
    assert asyncio.run(storage.effective_provider_api_key(row, "member")) == "shared-test"
    storage.get_user_provider_key.return_value = {"api_key": "personal-test"}
    assert asyncio.run(storage.effective_provider_api_key(row, "member")) == "personal-test"


def test_web_service_forwards_advanced_fields():
    from types import SimpleNamespace

    from melonclaw.services.chat import ChatService

    service = SimpleNamespace(
        conversations=SimpleNamespace(resolve_user=AsyncMock()),
        resources=SimpleNamespace(update_provider=AsyncMock()),
    )
    asyncio.run(ChatService.update_provider(
        service, "admin", "deepseek", api_key_env="TEST_API_KEY",
        request_headers={"X-Client": "test"}, extra_config={"enable_thinking": True},
    ))
    service.conversations.resolve_user.assert_awaited_once_with("admin")
    fields = service.resources.update_provider.call_args.kwargs
    assert fields["api_key_env"] == "TEST_API_KEY"
    assert fields["request_headers"] == {"X-Client": "test"}
    assert fields["extra_config"] == {"enable_thinking": True}
