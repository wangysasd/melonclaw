"""环境管理的安全输入、作用域与无业务环境继承边界。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from melonclaw.core.defaults import managed_env_name, resolve_agent_env
from melonclaw.services.environment import (
    EnvironmentError,
    EnvironmentService,
    environment_scope,
    mask_environment_value,
    validate_fields,
)
from melonclaw.services.environment_chat import parse_environment_chat


@pytest.mark.parametrize(
    "name",
    [
        "HOME",
        "LANG",
        "TZ",
        "LC_ALL",
        "PATH",
        "DATABASE_URL",
        "DEEPSEEK_API_KEY",
        "LANGFUSE_SECRET_KEY",
        "MELONCLAW_HOST",
        "PYTHONPATH",
        "LD_PRELOAD",
        "BASH_ENV",
    ],
)
def test_reserved_names_cannot_be_managed(name):
    assert not managed_env_name(name)


def test_only_base_and_explicit_database_values_enter_shell():
    result = resolve_agent_env(
        {
            "HOME": "/tmp/test",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "APP_SECRET": "deployment-value",
            "MELONCLAW_SHELL_ENV_ALLOWLIST": "APP_*",
        },
        {"APP_SECRET": "database-value", "HOME": "/bad", "LD_PRELOAD": "bad", "EMPTY": ""},
    )
    assert result == {
        "HOME": "/tmp/test",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "APP_SECRET": "database-value",
        "EMPTY": "",
    }
    assert "APP_SECRET" not in resolve_agent_env({"APP_SECRET": "deployment-value"})


@pytest.mark.parametrize(
    "content",
    [
        "设置环境变量 DEMO=fixture=value",
        "请帮我设置环境变量\nDEMO=fixture=value",
        "新增环境变量\n```env\nDEMO=fixture=value\n```",
    ],
)
def test_full_chat_assignment_is_redacted_and_retry_stable(content):
    kwargs = dict(user_id="alice", conversation_id="conversation", request_id="request")
    clean, draft = parse_environment_chat(content, **kwargs)
    assert "fixture" not in clean and "fixture=value" == draft["operation"]["fields"]["value"]
    assert (clean, draft) == parse_environment_chat(content, **kwargs)
    assert (
        draft["id"]
        != parse_environment_chat(content.replace("fixture=value", "other=value"), **kwargs)[1][
            "id"
        ]
    )


@pytest.mark.parametrize(
    "content",
    [
        "示例：设置环境变量 DEMO=fixture",
        "解释下面的配置\n设置环境变量 DEMO=fixture",
        "DEMO=fixture",
    ],
)
def test_quoted_examples_and_assignment_without_intent_do_not_write(content):
    assert parse_environment_chat(content, user_id="u", conversation_id="c", request_id="r") == (
        content,
        None,
    )


@pytest.mark.parametrize(
    "content",
    ["设置环境变量 DEMO", "设置环境变量\nA=fixture\nB=other", "启用环境变量 DEMO=fixture"],
)
def test_invalid_chat_input_has_safe_error(content):
    with pytest.raises(EnvironmentError) as error:
        parse_environment_chat(content, user_id="u", conversation_id="c", request_id="r")
    assert "fixture" not in str(error.value)


def test_unencodable_chat_value_is_rejected_without_echoing():
    content = "设置环境变量 TOKEN=\ud800"
    with pytest.raises(EnvironmentError) as error:
        parse_environment_chat(content, user_id="u", conversation_id="c", request_id="r")
    assert "\ud800" not in str(error.value)


def test_management_rejects_unencodable_value_as_validation_error():
    with pytest.raises(EnvironmentError, match="有效 UTF-8"):
        validate_fields("TOKEN", {"value": "\ud800"}, create=True)


def test_management_rejects_unencodable_description_as_validation_error():
    with pytest.raises(EnvironmentError, match="有效 UTF-8"):
        validate_fields("TOKEN", {"description": "用途\ud800"})


def test_toggle_delete_and_description_do_not_require_old_value():
    for text, action, fields in [
        ("停用环境变量 DEMO", "update", {"enabled": False}),
        ("删除环境变量 DEMO", "delete", {}),
        ("修改环境变量 DEMO 的说明：用途", "update", {"description": "用途"}),
    ]:
        _, draft = parse_environment_chat(text, user_id="u", conversation_id="c", request_id="r")
        assert draft["operation"] == {"name": "DEMO", "action": action, "fields": fields}


def test_admin_scope_and_management_response_never_contains_value():
    async def run():
        user = SimpleNamespace(user_id="alice", tenant_role="member")
        storage = SimpleNamespace(
            get_user_context=AsyncMock(return_value=user),
            list_environment=AsyncMock(return_value=[]),
            list_provider_rows=AsyncMock(return_value=[]),
        )
        service = EnvironmentService(SimpleNamespace(require_ready=lambda: storage))
        change = await service.change("alice", name="DEMO", fields={"value": ""})
        assert change["scope"] == "user" and change["owner_user_id"] == "alice"
        user.tenant_role = "admin"
        assert environment_scope(user) == "global"
        change = await service.change("alice", name="DEMO", fields={"value": "fixture"})
        assert change["scope"] == "global" and change["owner_user_id"] is None
        storage.list_provider_rows.return_value = [{"api_key_env": "DEMO"}]
        with pytest.raises(EnvironmentError):
            await service.change("alice", name="DEMO", fields={"value": "fixture"})

    asyncio.run(run())


def test_chat_values_keep_literal_quotes_backticks_and_dollar_references():
    _, draft = parse_environment_chat('设置环境变量 KEY="${HOME}```x=y"', user_id="u", conversation_id="c", request_id="r")
    assert draft["operation"]["fields"]["value"] == '"${HOME}```x=y"'


@pytest.mark.parametrize("value, expected", [
    ("", "***********"), ("abc", "***********"), ("12345678", "***********"),
    ("123456789", "1234***6789"), ("abcd-secret-wxyz", "abcd***wxyz"),
    ("一二三四五六七八九十", "一二三四***七八九十"),
])
def test_environment_value_preview(value, expected):
    assert mask_environment_value(value) == expected
