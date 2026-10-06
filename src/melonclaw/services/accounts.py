"""账户用例；管理功能不是 Agent 工具。"""
from __future__ import annotations

import asyncio
import hashlib
import secrets

from melonclaw.core.passwords import hash_password, verify_password


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccountService:
    def __init__(self, storage):
        self.storage = storage

    async def login(self, user_id, password, *, passwordless=False):
        async with self.storage.account_guard():
            context = await self.storage.get_user_context(user_id)
            encoded = await self.storage.account_password(user_id)
            valid = passwordless or await asyncio.to_thread(verify_password, password, encoded)
            if context is None or not valid:
                raise ValueError("用户 ID 或密码错误。")
            token = secrets.token_urlsafe(32)
            await self.storage.create_auth_session(token_digest(token), user_id)
            return token

    async def session(self, token):
        session = await self.storage.get_auth_session(token_digest(token))
        if session is None:
            return None
        current = await self.storage.get_user_context(session["user_id"])
        origin = await self.storage.get_user_context(session["login_user_id"])
        return current if origin is not None else None

    async def switch(self, token, user_id):
        async with self.storage.account_guard():
            if await self.session(token) is None:
                raise ValueError("登录已失效。")
            if await self.storage.get_user_context(user_id) is None:
                raise ValueError("用户不存在或所属租户已停用。")
            await self.storage.switch_auth_session(token_digest(token), user_id)

    async def save_user(self, user_id, name, tenant_id, password=None):
        if user_id == "admin" and tenant_id != "system":
            raise ValueError("内置 admin 不能更换租户。")
        values = {"user_name_zh": name, "tenant_id": tenant_id}
        if password is not None:
            values["password_hash"] = await asyncio.to_thread(hash_password, password)
        await self.storage.save_account_user(user_id, values, create=password is not None)

    async def delete_user(self, user_id):
        if user_id == "admin":
            raise ValueError("内置 admin 不能删除。")
        await self.storage.delete_account_user(user_id)

    async def reset_password(self, user_id, password):
        encoded = await asyncio.to_thread(hash_password, password)
        await self.storage.set_account_password(user_id, encoded)

    async def save_tenant(self, tenant_id, name, enabled, *, create=False):
        if tenant_id == "system" and not enabled:
            raise ValueError("内置 system 租户不能停用。")
        await self.storage.save_account_tenant(tenant_id, {
            "tenant_name_zh": name, "enabled": enabled,
        }, create=create)
