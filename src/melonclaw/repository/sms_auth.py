"""短信验证请求、配额和手机号绑定的短事务。"""

from __future__ import annotations

import hmac
import math
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from melonclaw.database.schema import (
    registration_verifications,
    sms_challenges,
    tenants,
    user_phones,
    users,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SEND_STATUSES = frozenset({"sent", "failed", "uncertain"})
_ATTEMPT_STATUSES = frozenset({"sent", "failed", "uncertain"})
# 下次发码事务里删除更早的记录。这不是验证码有效期，也不是定时任务。
SMS_RECORD_CLEANUP_HOURS = 48


class SmsQuotaError(Exception):
    def __init__(self, retry_after: int):
        super().__init__("发送过于频繁，请稍后再试。")
        self.retry_after = max(int(retry_after), 1)


class SmsBusyError(SmsQuotaError):
    """同一号码已有一次发送尚未结束。"""


class SmsIdentityConflict(Exception):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


class ChallengeLost(Exception):
    """重发或并发已经使这次核验不能提交。"""


def _retry_after(now: datetime, started: datetime, interval: int) -> int:
    remaining = interval - (now - started).total_seconds()
    return max(1, math.ceil(remaining))


def seconds_until_next_shanghai_day(now: datetime) -> int:
    local = now.astimezone(_SHANGHAI)
    tomorrow = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return max(1, math.ceil((tomorrow - local).total_seconds()))


def _same_secret(left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


class SmsAuthRepositoryMixin:
    async def phone_tail(self, user_id: str) -> str | None:
        async with self.engine.connect() as conn:
            value = await conn.scalar(
                select(user_phones.c.phone_tail).where(user_phones.c.user_id == user_id)
            )
        return str(value) if value else None

    async def reserve_sms_challenge(
        self, *, phone_hmac: str, purpose: str, user_id: str | None, scheme_name: str,
        out_id: str, challenge_id: str, expires_at: datetime, now: datetime, day: str,
        interval_seconds: int, phone_daily_limit: int, ip_daily_limit: int,
        global_daily_limit: int, source_ip: str, stale_seconds: int,
    ) -> None:
        if purpose not in {"register", "login", "bind", "reset"}:
            raise RuntimeError("短信用途无效。")
        if purpose == "bind" and not user_id:
            raise RuntimeError("绑定验证码必须关联用户。")
        cutoff = now - timedelta(hours=SMS_RECORD_CLEANUP_HOURS)
        stale_before = now - timedelta(seconds=stale_seconds)
        async with self.engine.begin() as conn:
            await conn.execute(text(
                "SELECT pg_advisory_xact_lock(hashtext(current_schema()), hashtext(:phone))"
            ), {"phone": phone_hmac})
            await conn.execute(text(
                "DELETE FROM sms_challenges WHERE created_at < :cutoff"
            ), {"cutoff": cutoff})
            await conn.execute(text(
                "DELETE FROM sms_quotas WHERE updated_at < :cutoff"
            ), {"cutoff": cutoff})
            await conn.execute(text("""
                UPDATE sms_challenges
                SET status = 'uncertain', updated_at = :now
                WHERE phone_hmac = :phone AND purpose = :purpose
                  AND status = 'reserved' AND updated_at < :stale_before
            """), {
                "now": now, "phone": phone_hmac, "purpose": purpose, "stale_before": stale_before,
            })
            active = await conn.scalar(text("""
                SELECT updated_at FROM sms_challenges
                WHERE phone_hmac = :phone AND purpose = :purpose AND status = 'reserved'
            """), {"phone": phone_hmac, "purpose": purpose})
            if active is not None:
                started = await conn.scalar(text(
                    "SELECT window_start FROM sms_quotas WHERE bucket = :bucket"
                ), {"bucket": f"gap:{phone_hmac}"})
                retry = _retry_after(now, started or active, interval_seconds)
                raise SmsBusyError(retry)
            await self._take_interval(conn, phone_hmac, now, interval_seconds)
            await self._take_daily(
                conn, f"day:phone:{phone_hmac}:{day}", now, phone_daily_limit,
            )
            if source_ip:
                await self._take_daily(
                    conn, f"day:ip:{source_ip[:64]}:{day}", now, ip_daily_limit,
                )
            await self._take_daily(conn, f"day:all:{day}", now, global_daily_limit)
            await conn.execute(text("""
                UPDATE sms_challenges
                SET status = 'replaced', updated_at = :now
                WHERE phone_hmac = :phone AND purpose = :purpose
                  AND status IN ('sent', 'verifying')
            """), {"now": now, "phone": phone_hmac, "purpose": purpose})
            generation = await conn.scalar(text("""
                SELECT COALESCE(MAX(generation), 0) + 1 FROM sms_challenges
                WHERE phone_hmac = :phone AND purpose = :purpose
            """), {"phone": phone_hmac, "purpose": purpose})
            await conn.execute(sms_challenges.insert().values(
                challenge_id=challenge_id, purpose=purpose, phone_hmac=phone_hmac,
                user_id=user_id, generation=generation, status="reserved", out_id=out_id,
                scheme_name=scheme_name, attempts=0, expires_at=expires_at,
                created_at=now, updated_at=now,
            ))

    async def _take_interval(self, conn, phone_hmac: str, now: datetime, interval: int) -> None:
        bucket = f"gap:{phone_hmac}"
        granted = await conn.execute(text("""
            INSERT INTO sms_quotas (bucket, window_start, count, updated_at)
            VALUES (:bucket, :now, 1, :now)
            ON CONFLICT (bucket) DO UPDATE
            SET window_start = EXCLUDED.window_start, count = 1, updated_at = EXCLUDED.updated_at
            WHERE sms_quotas.window_start <= :deadline
            RETURNING window_start
        """), {"bucket": bucket, "now": now, "deadline": now - timedelta(seconds=interval)})
        if granted.first() is not None:
            return
        started = await conn.scalar(text(
            "SELECT window_start FROM sms_quotas WHERE bucket = :bucket"
        ), {"bucket": bucket})
        raise SmsQuotaError(_retry_after(now, started, interval))

    async def _take_daily(self, conn, bucket: str, now: datetime, limit: int) -> None:
        granted = await conn.execute(text("""
            INSERT INTO sms_quotas (bucket, window_start, count, updated_at)
            VALUES (:bucket, :now, 1, :now)
            ON CONFLICT (bucket) DO UPDATE
            SET count = sms_quotas.count + 1, updated_at = EXCLUDED.updated_at
            WHERE sms_quotas.count < :limit
            RETURNING count
        """), {"bucket": bucket, "now": now, "limit": limit})
        if granted.first() is None:
            raise SmsQuotaError(seconds_until_next_shanghai_day(now))

    async def finish_sms_send(self, challenge_id: str, status: str, now: datetime) -> bool:
        if status not in _SEND_STATUSES:
            raise RuntimeError("短信发送状态无效。")
        async with self.engine.begin() as conn:
            result = await conn.execute(text("""
                UPDATE sms_challenges SET status = :status, updated_at = :now
                WHERE challenge_id = :challenge_id AND status = 'reserved'
            """), {"status": status, "now": now, "challenge_id": challenge_id})
            return bool(result.rowcount)

    async def claim_sms_attempt(
        self, *, challenge_id: str, purpose: str, phone_hmac: str, user_id: str | None,
        max_attempts: int, now: datetime,
    ) -> dict | None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(text("""
                UPDATE sms_challenges
                SET status = 'verifying', attempts = attempts + 1, updated_at = :now
                WHERE challenge_id = :challenge_id AND purpose = :purpose
                  AND phone_hmac = :phone AND status = 'sent' AND expires_at > :now
                  AND attempts < :max_attempts AND user_id IS NOT DISTINCT FROM :user_id
                RETURNING generation, out_id, scheme_name, attempts, expires_at
            """), {
                "now": now, "challenge_id": challenge_id, "purpose": purpose,
                "phone": phone_hmac, "max_attempts": max_attempts, "user_id": user_id,
            })).mappings().first()
        return dict(row) if row else None

    async def settle_sms_attempt(
        self, challenge_id: str, generation: int, status: str, now: datetime,
    ) -> None:
        if status not in _ATTEMPT_STATUSES:
            raise RuntimeError("短信核验状态无效。")
        async with self.engine.begin() as conn:
            await conn.execute(text("""
                UPDATE sms_challenges SET status = :status, updated_at = :now
                WHERE challenge_id = :challenge_id AND generation = :generation
                  AND status = 'verifying'
            """), {
                "status": status, "now": now, "challenge_id": challenge_id,
                "generation": generation,
            })

    async def _consume_challenge(
        self, conn, *, challenge_id: str, generation: int, purpose: str,
        phone_hmac: str, user_id: str | None, now: datetime,
    ) -> None:
        result = await conn.execute(text("""
            UPDATE sms_challenges SET status = 'consumed', updated_at = :now
            WHERE challenge_id = :challenge_id AND status = 'verifying'
              AND generation = :generation AND expires_at > :now
              AND purpose = :purpose AND phone_hmac = :phone
              AND user_id IS NOT DISTINCT FROM :user_id
        """), {
            "now": now, "challenge_id": challenge_id, "generation": generation,
            "purpose": purpose, "phone": phone_hmac, "user_id": user_id,
        })
        if not result.rowcount:
            raise ChallengeLost()

    async def register_verified_user(
        self, *, challenge_id: str, generation: int, user_id: str, display_name: str,
        password_hash: str, phone_hmac: str, phone_tail: str, verified_at: datetime, now: datetime,
    ) -> None:
        try:
            async with self.account_guard(exclusive=True) as conn:
                await self._consume_challenge(
                    conn, challenge_id=challenge_id, generation=generation, purpose="register",
                    phone_hmac=phone_hmac, user_id=None, now=now,
                )
                if await conn.scalar(select(users.c.user_id).where(users.c.user_id == user_id)):
                    raise SmsIdentityConflict("用户 ID 已存在，请更换后重试。", retryable=True)
                await conn.execute(delete(user_phones).where(
                    user_phones.c.phone_hmac == phone_hmac,
                    user_phones.c.user_id.in_(select(users.c.user_id).where(
                        users.c.tenant_status == "deleted",
                    )),
                ))
                if await conn.scalar(
                    select(user_phones.c.user_id).where(user_phones.c.phone_hmac == phone_hmac)
                ):
                    raise SmsIdentityConflict("该手机号已绑定其他账号。", retryable=False)
                tenant_id = "u" + secrets.token_hex(8)
                tenant_name = f"用户{user_id}"
                if len(tenant_name) > 64 or await conn.scalar(
                    select(tenants.c.tenant_id).where(tenants.c.tenant_name_zh == tenant_name)
                ):
                    tenant_name = "用户" + secrets.token_hex(8)
                await conn.execute(tenants.insert().values(
                    tenant_id=tenant_id, tenant_name_zh=tenant_name, enabled=True,
                    created_at=now, updated_at=now,
                ))
                await conn.execute(users.insert().values(
                    user_id=user_id, tenant_id=tenant_id, user_name_zh=display_name,
                    tenant_role="member", tenant_status="active", password_hash=password_hash,
                    created_at=now, updated_at=now,
                ))
                await conn.execute(user_phones.insert().values(
                    user_id=user_id, phone_hmac=phone_hmac, phone_tail=phone_tail,
                    created_at=now, updated_at=now,
                ))
                await conn.execute(registration_verifications.insert().values(
                    user_id=user_id, verification_id=challenge_id, method="sms_code",
                    phone_tail=phone_tail, verified_at=verified_at,
                ))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23505":
                raise
            raise SmsIdentityConflict("账号或手机号已存在。", retryable=False) from exc

    async def login_verified_phone(
        self, *, challenge_id: str, generation: int, phone_hmac: str, now: datetime,
    ) -> dict:
        async with self.account_guard() as conn:
            await self._consume_challenge(
                conn, challenge_id=challenge_id, generation=generation, purpose="login",
                phone_hmac=phone_hmac, user_id=None, now=now,
            )
            status = await conn.scalar(
                select(users.c.tenant_status)
                .select_from(user_phones.join(users, user_phones.c.user_id == users.c.user_id))
                .where(user_phones.c.phone_hmac == phone_hmac)
            )
            if status is None:
                return {"kind": "unregistered"}
            if status != "active":
                return {"kind": "unavailable"}
            user_id = await conn.scalar(
                select(user_phones.c.user_id).where(user_phones.c.phone_hmac == phone_hmac)
            )
            return {"kind": "user", "user_id": str(user_id)}

    async def bind_verified_phone(
        self, *, challenge_id: str, generation: int, user_id: str, expected_password_hash: str,
        phone_hmac: str, phone_tail: str, now: datetime,
    ) -> None:
        try:
            async with self.account_guard(exclusive=True) as conn:
                current = await conn.scalar(select(users.c.password_hash).where(
                    users.c.user_id == user_id, users.c.tenant_status == "active",
                ))
                if current is None:
                    raise SmsIdentityConflict("用户不存在或已删除。", retryable=False)
                if not _same_secret(current, expected_password_hash):
                    raise SmsIdentityConflict("密码已变化，请重新验证。", retryable=True)
                if await conn.scalar(
                    select(user_phones.c.user_id).where(user_phones.c.user_id == user_id)
                ):
                    raise SmsIdentityConflict("当前账号已绑定手机号。", retryable=False)
                owner = await conn.scalar(
                    select(user_phones.c.user_id).where(user_phones.c.phone_hmac == phone_hmac)
                )
                if owner:
                    raise SmsIdentityConflict("该手机号已绑定其他账号。", retryable=False)
                await self._consume_challenge(
                    conn, challenge_id=challenge_id, generation=generation, purpose="bind",
                    phone_hmac=phone_hmac, user_id=user_id, now=now,
                )
                await conn.execute(user_phones.insert().values(
                    user_id=user_id, phone_hmac=phone_hmac, phone_tail=phone_tail,
                    created_at=now, updated_at=now,
                ))
        except IntegrityError as exc:
            raise SmsIdentityConflict("该手机号已绑定其他账号。", retryable=False) from exc

    async def reset_password_verified_phone(
        self, *, challenge_id: str, generation: int, phone_hmac: str,
        password_hash: str, now: datetime,
    ) -> dict:
        async with self.account_guard(exclusive=True) as conn:
            await self._consume_challenge(
                conn, challenge_id=challenge_id, generation=generation, purpose="reset",
                phone_hmac=phone_hmac, user_id=None, now=now,
            )
            row = (await conn.execute(
                select(users.c.user_id, users.c.tenant_status)
                .select_from(user_phones.join(users, user_phones.c.user_id == users.c.user_id))
                .where(user_phones.c.phone_hmac == phone_hmac)
            )).mappings().first()
            if row is None:
                return {"kind": "unregistered"}
            user_id = str(row["user_id"])
            if row["tenant_status"] != "active" or user_id == "admin":
                return {"kind": "unavailable"}
            result = await conn.execute(
                update(users)
                .where(users.c.user_id == user_id, users.c.tenant_status == "active")
                .values(password_hash=password_hash, updated_at=now)
            )
            if not result.rowcount:
                return {"kind": "unavailable"}
            return {"kind": "reset", "user_id": user_id}
