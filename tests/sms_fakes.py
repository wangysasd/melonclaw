"""短信流程测试替身。配额和核销规则与仓储一致，供不连接数据库的用例使用。"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

from melonclaw.repository.models import UserContext
from melonclaw.repository.sms_auth import (
    ChallengeLost,
    SmsBusyError,
    SmsIdentityConflict,
    SmsQuotaError,
    _retry_after,
    seconds_until_next_shanghai_day,
)
from melonclaw.services.sms_provider import SmsCallResult


def sms_settings(**overrides):
    values = {
        "sms_enabled": True,
        "registration_enabled": True,
        "phone_hmac_key": "k" * 32,
        "profile": "prod",
        "sms_code_ttl_seconds": 300,
        "sms_phone_interval_seconds": 60,
        "sms_phone_daily_limit": 10,
        "sms_ip_daily_limit": 30,
        "sms_global_daily_limit": 200,
        "sms_timeout_seconds": 8,
        "sms_max_attempts": 5,
        "sms_sign_name": "测试签名",
        "sms_template_auth": "100001",
        "sms_template_bind": "100002",
        "sms_template_reset": "",
        "sms_code_variable": "code",
        "sms_minutes_variable": "min",
        "sms_access_key_id": "test-key-id",
        "sms_access_key_secret": "test-key-secret",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeSms:
    def __init__(self):
        self.sent = []
        self.checks = []
        self.send_result = SmsCallResult("sent")
        self.check_result = SmsCallResult("pass")
        self.send_error = None

    async def send(self, request):
        self.sent.append(request)
        if self.send_error is not None:
            raise self.send_error
        return self.send_result

    async def check(self, request):
        self.checks.append(request)
        return self.check_result


class MemoryPhoneStore:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.users: dict[str, UserContext] = {}
        self.passwords: dict[str, str] = {}
        self.phones: dict[str, tuple[str, str]] = {}
        self.hmac_owner: dict[str, str] = {}
        self.verifications: dict[str, dict] = {}
        self.challenges: dict[str, dict] = {}
        self.quotas: dict[str, dict] = {}
        self.tenants: list[str] = []

    @asynccontextmanager
    async def account_guard(self, **kwargs):
        async with self._lock:
            yield None

    async def active_user(self, user_id, connection=None):
        user = self.users.get(user_id)
        if user is None or user.tenant_status != "active":
            return None
        return user

    async def account_password(self, user_id):
        user = self.users.get(user_id)
        if user is None or user.tenant_status != "active":
            return None
        return self.passwords.get(user_id)

    async def phone_tail(self, user_id):
        phone = self.phones.get(user_id)
        return phone[1] if phone else None

    async def reserve_sms_challenge(self, **kwargs):
        async with self._lock:
            now = kwargs["now"]
            phone = kwargs["phone_hmac"]
            purpose = kwargs["purpose"]
            cutoff = now - timedelta(hours=48)
            self.challenges = {
                key: row for key, row in self.challenges.items() if row["created_at"] >= cutoff
            }
            self.quotas = {
                key: row for key, row in self.quotas.items() if row["updated_at"] >= cutoff
            }
            stale_before = now - timedelta(seconds=kwargs["stale_seconds"])
            for row in self.challenges.values():
                if (row["phone_hmac"] == phone and row["purpose"] == purpose
                        and row["status"] == "reserved" and row["updated_at"] < stale_before):
                    row["status"] = "uncertain"
                    row["updated_at"] = now
            if any(row["phone_hmac"] == phone and row["purpose"] == purpose and row["status"] == "reserved"
                   for row in self.challenges.values()):
                started = self.quotas.get(f"gap:{phone}", {}).get("window_start", now)
                raise SmsBusyError(_retry_after(now, started, kwargs["interval_seconds"]))
            self._take_interval(phone, now, kwargs["interval_seconds"])
            self._take_daily(f"day:phone:{phone}:{kwargs['day']}", now, kwargs["phone_daily_limit"])
            if kwargs["source_ip"]:
                self._take_daily(
                    f"day:ip:{kwargs['source_ip'][:64]}:{kwargs['day']}", now, kwargs["ip_daily_limit"],
                )
            self._take_daily(f"day:all:{kwargs['day']}", now, kwargs["global_daily_limit"])
            for row in self.challenges.values():
                if row["phone_hmac"] == phone and row["purpose"] == purpose and row["status"] in {"sent", "verifying"}:
                    row["status"] = "replaced"
                    row["updated_at"] = now
            generation = 1 + max(
                (row["generation"] for row in self.challenges.values()
                 if row["phone_hmac"] == phone and row["purpose"] == purpose),
                default=0,
            )
            self.challenges[kwargs["challenge_id"]] = {
                "purpose": purpose, "phone_hmac": phone, "user_id": kwargs["user_id"],
                "generation": generation, "status": "reserved", "out_id": kwargs["out_id"],
                "scheme_name": kwargs["scheme_name"], "attempts": 0,
                "expires_at": kwargs["expires_at"], "created_at": now, "updated_at": now,
            }

    def _take_interval(self, phone, now, interval):
        bucket = f"gap:{phone}"
        row = self.quotas.get(bucket)
        if row is None or row["window_start"] <= now - timedelta(seconds=interval):
            self.quotas[bucket] = {"window_start": now, "count": 1, "updated_at": now}
            return
        raise SmsQuotaError(_retry_after(now, row["window_start"], interval))

    def _take_daily(self, bucket, now, limit):
        row = self.quotas.get(bucket)
        if row is None:
            self.quotas[bucket] = {"window_start": now, "count": 1, "updated_at": now}
            return
        if row["count"] >= limit:
            raise SmsQuotaError(seconds_until_next_shanghai_day(now))
        row["count"] += 1
        row["updated_at"] = now

    async def finish_sms_send(self, challenge_id, status, now):
        async with self._lock:
            row = self.challenges.get(challenge_id)
            if row is None or row["status"] != "reserved":
                return False
            row["status"] = status
            row["updated_at"] = now
            return True

    async def claim_sms_attempt(self, *, challenge_id, purpose, phone_hmac, user_id, max_attempts, now):
        async with self._lock:
            row = self.challenges.get(challenge_id)
            if (row is None or row["purpose"] != purpose or row["phone_hmac"] != phone_hmac
                    or row["status"] != "sent" or row["expires_at"] <= now
                    or row["attempts"] >= max_attempts or row["user_id"] != user_id):
                return None
            row["status"] = "verifying"
            row["attempts"] += 1
            row["updated_at"] = now
            return {
                "generation": row["generation"], "out_id": row["out_id"],
                "scheme_name": row["scheme_name"], "attempts": row["attempts"],
                "expires_at": row["expires_at"],
            }

    async def settle_sms_attempt(self, challenge_id, generation, status, now):
        async with self._lock:
            row = self.challenges.get(challenge_id)
            if row and row["status"] == "verifying" and row["generation"] == generation:
                row["status"] = status
                row["updated_at"] = now

    def _consume(self, challenge_id, generation, now):
        row = self.challenges.get(challenge_id)
        if row is None or row["status"] != "verifying" or row["generation"] != generation or row["expires_at"] <= now:
            raise ChallengeLost()
        row["status"] = "consumed"
        row["updated_at"] = now

    async def register_verified_user(
        self, *, challenge_id, generation, user_id, display_name, password_hash,
        phone_hmac, phone_tail, verified_at, now,
    ):
        async with self._lock:
            if user_id in self.users:
                raise SmsIdentityConflict("用户 ID 已存在，请更换后重试。", retryable=True)
            if phone_hmac in self.hmac_owner:
                raise SmsIdentityConflict("该手机号已绑定其他账号。", retryable=False)
            self._consume(challenge_id, generation, now)
            tenant_id = "u" + secrets.token_hex(4)
            self.tenants.append(tenant_id)
            self.users[user_id] = UserContext(
                user_id, display_name, tenant_id, f"用户{user_id}", "member", "active", True,
            )
            self.passwords[user_id] = password_hash
            self.phones[user_id] = (phone_hmac, phone_tail)
            self.hmac_owner[phone_hmac] = user_id
            self.verifications[user_id] = {
                "verification_id": challenge_id,
                "phone_tail": phone_tail,
                "verified_at": verified_at,
                "created_at": now,
            }

    async def account_users(self, *, include_deleted_registrations=False):
        items = []
        for user in self.users.values():
            record = self.verifications.get(user.user_id)
            if user.tenant_status != "active" and not (include_deleted_registrations and record):
                continue
            items.append({
                "user_id": user.user_id,
                "user_name_zh": user.user_name_zh,
                "tenant_id": user.tenant_id,
                "tenant_name_zh": user.tenant_name_zh,
                "tenant_role": user.tenant_role,
                "tenant_status": user.tenant_status,
                "tenant_enabled": user.tenant_enabled,
                "created_at": record["created_at"] if record else None,
                "phone_verified": record is not None,
                "phone_tail": record["phone_tail"] if record else None,
                "verification_method": "短信验证码" if record else None,
                "verified_at": record["verified_at"] if record else None,
                "verification_id": record["verification_id"] if record else None,
            })
        return items

    async def login_verified_phone(self, *, challenge_id, generation, phone_hmac, now):
        async with self._lock:
            self._consume(challenge_id, generation, now)
            owner = self.hmac_owner.get(phone_hmac)
            if owner is None:
                return {"kind": "unregistered"}
            if self.users[owner].tenant_status != "active":
                return {"kind": "unavailable"}
            return {"kind": "user", "user_id": owner}

    async def bind_verified_phone(
        self, *, challenge_id, generation, user_id, expected_password_hash,
        phone_hmac, phone_tail, now,
    ):
        async with self._lock:
            user = self.users.get(user_id)
            if user is None or user.tenant_status != "active":
                raise SmsIdentityConflict("用户不存在或已删除。", retryable=False)
            current = self.passwords.get(user_id)
            if current != expected_password_hash:
                raise SmsIdentityConflict("密码已变化，请重新验证。", retryable=True)
            if user_id in self.phones:
                raise SmsIdentityConflict("当前账号已绑定手机号。", retryable=False)
            if phone_hmac in self.hmac_owner:
                raise SmsIdentityConflict("该手机号已绑定其他账号。", retryable=False)
            self._consume(challenge_id, generation, now)
            self.phones[user_id] = (phone_hmac, phone_tail)
            self.hmac_owner[phone_hmac] = user_id

    async def reset_password_verified_phone(
        self, *, challenge_id, generation, phone_hmac, password_hash, now,
    ):
        async with self._lock:
            self._consume(challenge_id, generation, now)
            owner = self.hmac_owner.get(phone_hmac)
            if owner is None:
                return {"kind": "unregistered"}
            user = self.users.get(owner)
            if user is None or user.tenant_status != "active" or owner == "admin":
                return {"kind": "unavailable"}
            self.passwords[owner] = password_hash
            return {"kind": "reset", "user_id": owner}
