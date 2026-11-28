"""账户用例；管理功能不是 Agent 工具。"""
from __future__ import annotations

import asyncio

from melonclaw.core.auth_token import issue_token, token_subject
from melonclaw.core.config import load_settings
from melonclaw.core.passwords import hash_password, verify_password


class AccountService:
    def __init__(self, storage, sms=None, settings=None, clock=None):
        self.storage = storage
        self.sms = sms
        self.settings = settings
        self.clock = clock
        self._sms_client = None

    async def login(self, user_id, password, *, passwordless=False):
        async with self.storage.account_guard():
            context = await self.storage.active_user(user_id)
            encoded = await self.storage.account_password(user_id)
            valid = passwordless or await asyncio.to_thread(verify_password, password, encoded)
            if context is None or not valid:
                raise ValueError("用户 ID 或密码错误。")
            return self._issue(context.user_id)

    async def session(self, token):
        user_id = token_subject(token or "", load_settings().jwt_secret)
        if user_id is None:
            return None
        return await self.storage.active_user(user_id)

    async def switch(self, token, user_id):
        async with self.storage.account_guard():
            if await self.session(token) is None:
                raise ValueError("登录已失效。")
            if await self.storage.active_user(user_id) is None:
                raise ValueError("用户不存在或已删除。")
            return self._issue(user_id)

    def _issue(self, user_id: str) -> str:
        settings = load_settings()
        return issue_token(user_id, settings.jwt_secret, settings.token_ttl_hours)

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

    async def update_name(self, user_id, name):
        cleaned = str(name or "").strip()
        if not 1 <= len(cleaned) <= 64:
            raise ValueError("昵称需要 1～64 个字符。")
        await self.storage.set_account_name(user_id, cleaned)
        return cleaned

    def _runtime_settings(self):
        return self.settings if self.settings is not None else load_settings()

    def _now(self):
        from datetime import UTC, datetime

        return self.clock() if self.clock is not None else datetime.now(UTC)

    def _sms_provider(self, settings):
        if self.sms is not None:
            return self.sms
        if self._sms_client is None:
            from melonclaw.services.sms_provider import AliyunSmsProvider

            self._sms_client = AliyunSmsProvider(settings)
        return self._sms_client

    async def send_sms_challenge(self, phone, purpose, source_ip, user_id=None):
        from melonclaw.services.phone_auth import send_phone_challenge

        settings = self._runtime_settings()
        return await send_phone_challenge(
            self.storage, lambda: self._sms_provider(settings), settings,
            phone=phone, purpose=purpose, source_ip=source_ip, user_id=user_id, now=self._now(),
        )

    async def register_with_sms(self, user_id, password, confirm_password, phone, challenge_id, code):
        from melonclaw.services.phone_auth import register_with_phone

        settings = self._runtime_settings()
        account = await register_with_phone(
            self.storage, lambda: self._sms_provider(settings), settings,
            user_id=user_id, password=password, confirm_password=confirm_password, phone=phone,
            challenge_id=challenge_id, code=code, clock=self._now,
        )
        return self._issue(account)

    async def login_with_sms(self, phone, challenge_id, code):
        from melonclaw.services.phone_auth import login_with_phone

        settings = self._runtime_settings()
        account = await login_with_phone(
            self.storage, lambda: self._sms_provider(settings), settings,
            phone=phone, challenge_id=challenge_id, code=code, clock=self._now,
        )
        return self._issue(account), account

    async def bind_phone(self, user_id, password, phone, challenge_id, code):
        from melonclaw.services.phone_auth import bind_phone

        settings = self._runtime_settings()
        return await bind_phone(
            self.storage, lambda: self._sms_provider(settings), settings,
            user_id=user_id, password=password, phone=phone, challenge_id=challenge_id,
            code=code, clock=self._now,
        )

    async def reset_password_with_sms(self, phone, challenge_id, code, password, confirm_password):
        from melonclaw.services.phone_auth import reset_password_with_phone

        settings = self._runtime_settings()
        return await reset_password_with_phone(
            self.storage, lambda: self._sms_provider(settings), settings,
            phone=phone, challenge_id=challenge_id, code=code,
            password=password, confirm_password=confirm_password, clock=self._now,
        )

    async def save_tenant(self, tenant_id, name, enabled, *, create=False):
        if tenant_id == "system" and not enabled:
            raise ValueError("内置 system 租户不能停用。")
        await self.storage.save_account_tenant(tenant_id, {
            "tenant_name_zh": name, "enabled": enabled,
        }, create=create)
