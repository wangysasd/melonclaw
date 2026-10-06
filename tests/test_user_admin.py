"""账户输入、密码和内置记录保护。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from melonclaw.api.routes.accounts import UserCreate, UserEdit
from melonclaw.core.passwords import hash_password, verify_password
from melonclaw.services.accounts import AccountService


def test_password_hash_is_salted_and_checks_exact_password():
    password = " secret-password "
    encoded = hash_password(password)
    assert encoded != hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password(password.strip(), encoded)
    assert not verify_password(password, None)
    assert password not in encoded
    with pytest.raises(ValueError):
        hash_password("tiny")


def test_create_accepts_full_name_and_rejects_invalid_ids_and_immutable_edits():
    body = UserCreate(user_id="u-1", user_name_zh="  张三的全名  ", tenant_id="team", password="new-password", confirm_password="new-password")
    assert body.user_name_zh == "张三的全名"
    assert "new-password" not in repr(body)
    with pytest.raises(ValidationError):
        UserCreate(user_id="a b", user_name_zh="用户", tenant_id="t", password="new-password", confirm_password="new-password")
    with pytest.raises(ValidationError):
        UserEdit(user_id="changed", user_name_zh="用户", tenant_id="t")


def test_account_service_protects_admin_and_hashes_new_password():
    async def run():
        storage = SimpleNamespace(save_account_user=AsyncMock(), delete_account_user=AsyncMock(), save_account_tenant=AsyncMock())
        service = AccountService(storage)
        for action in [service.delete_user("admin"), service.save_user("admin", "管理员", "another"), service.save_tenant("system", "系统", False)]:
            with pytest.raises(ValueError):
                await action
        await service.save_user("alice", "用户长名称", "team", "new-password")
        args = storage.save_account_user.call_args
        assert args.kwargs == {"create": True}
        assert args.args[1]["tenant_id"] == "team"
        assert verify_password("new-password", args.args[1]["password_hash"])
        storage.delete_account_user.assert_not_called()
    asyncio.run(run())
