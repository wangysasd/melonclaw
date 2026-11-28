"""把一次短信核验绑定到注册、登录或首次绑号。云端调用放在数据库事务外。"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from melonclaw.core.passwords import hash_password, verify_password
from melonclaw.core.phone import normalize_mainland_phone, phone_digest
from melonclaw.repository.accounts import AccountConflict
from melonclaw.repository.sms_auth import (
    ChallengeLost,
    SmsBusyError,
    SmsIdentityConflict,
    SmsQuotaError,
)
from melonclaw.services.sms_errors import SmsRequestError
from melonclaw.services.sms_provider import (
    SmsCallResult,
    SmsCheckRequest,
    SmsSendRequest,
    interpret_exception,
)

logger = logging.getLogger("melonclaw.sms")
_PURPOSES = frozenset({"register", "login", "bind", "reset"})
_LIMIT_CODES = frozenset({"BUSINESS_LIMIT_CONTROL", "FREQUENCY_FAIL"})


def _observe(action: str, purpose: str, result: str) -> None:
    logger.info("sms %s purpose=%s result=%s", action, purpose, result)


def _scheme(profile: str, purpose: str) -> str:
    environment = profile if profile in {"dev", "prod"} else "app"
    scheme = f"{environment}-{purpose}"
    if purpose not in _PURPOSES or len(scheme) > 20:
        raise RuntimeError("短信方案名称无效。")
    return scheme


def _template(settings, purpose: str) -> tuple[str, str]:
    import json

    minutes = max(1, math.ceil(settings.sms_code_ttl_seconds / 60))
    payload = {
        settings.sms_code_variable: "##code##",
        settings.sms_minutes_variable: str(minutes),
    }
    if purpose == "bind":
        template = settings.sms_template_bind
    elif purpose == "reset":
        template = getattr(settings, "sms_template_reset", "") or settings.sms_template_auth
    else:
        template = settings.sms_template_auth
    return template, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _require_sms(settings, purpose: str) -> None:
    if not settings.sms_enabled:
        raise SmsRequestError("短信验证未启用。", 403)
    if purpose == "register" and not settings.registration_enabled:
        raise SmsRequestError("自助注册未启用。", 403)
    if len(getattr(settings, "phone_hmac_key", "")) < 32:
        raise SmsRequestError("短信服务暂不可用，请稍后重试。", 503)


def _phone(value: str):
    try:
        return normalize_mainland_phone(value)
    except ValueError as exc:
        raise SmsRequestError(str(exc)) from None


def _challenge(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{32}", value or "") is None:
        raise SmsRequestError("验证码无效或已过期。")
    return value


def _verification_code(value: str) -> str:
    if re.fullmatch(r"\d{6}", value or "") is None:
        raise SmsRequestError("请输入 6 位数字验证码。")
    return value


def _vendor_limited(code: str) -> bool:
    return code.rsplit(".", 1)[-1].upper() in _LIMIT_CODES


async def _call(factory: Callable, method: str, request) -> SmsCallResult:
    try:
        return await getattr(factory(), method)(request)
    except Exception as exc:
        return interpret_exception(exc)


async def send_phone_challenge(
    storage, provider: Callable, settings, *, phone: str, purpose: str, source_ip: str,
    user_id: str | None, now: datetime,
) -> dict:
    if purpose not in {"register", "login", "reset"} and not (purpose == "bind" and user_id):
        raise SmsRequestError("短信用途无效。")
    _require_sms(settings, purpose)
    if purpose == "bind":
        if user_id == "admin":
            raise SmsRequestError("内置管理员请继续使用密码登录。", 403)
        if await storage.phone_tail(user_id):
            raise SmsRequestError("当前账号已绑定手机号。")
    number = _phone(phone)
    digest = phone_digest(number.e164, settings.phone_hmac_key)
    challenge_id = uuid4().hex
    out_id = secrets.token_hex(8)
    day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    try:
        await storage.reserve_sms_challenge(
            phone_hmac=digest, purpose=purpose, user_id=user_id,
            scheme_name=_scheme(settings.profile, purpose), out_id=out_id,
            challenge_id=challenge_id,
            expires_at=now + timedelta(seconds=settings.sms_code_ttl_seconds), now=now, day=day,
            interval_seconds=settings.sms_phone_interval_seconds,
            phone_daily_limit=settings.sms_phone_daily_limit,
            ip_daily_limit=settings.sms_ip_daily_limit,
            global_daily_limit=settings.sms_global_daily_limit,
            source_ip=source_ip or "",
            stale_seconds=settings.sms_timeout_seconds + 2,
        )
    except SmsBusyError as exc:
        _observe("send", purpose, "limited")
        raise SmsRequestError(
            f"验证码正在发送，请在 {exc.retry_after} 秒后重试。", 429, exc.retry_after,
        ) from None
    except SmsQuotaError as exc:
        _observe("send", purpose, "limited")
        raise SmsRequestError(
            f"发送过于频繁，请在 {exc.retry_after} 秒后重试。", 429, exc.retry_after,
        ) from None
    template_code, template_param = _template(settings, purpose)
    result = await _call(provider, "send", SmsSendRequest(
        phone_number=number.national, country_code="86", sign_name=settings.sms_sign_name,
        template_code=template_code, template_param=template_param,
        scheme_name=_scheme(settings.profile, purpose), out_id=out_id, code_length=6,
        valid_time=settings.sms_code_ttl_seconds, interval=settings.sms_phone_interval_seconds,
        code_type=1, return_verify_code=False, duplicate_policy=1, auto_retry=0,
    ))
    if result.kind == "sent":
        if not await storage.finish_sms_send(challenge_id, "sent", now):
            _observe("send", purpose, "unconfirmed")
            raise SmsRequestError("短信发送结果未能确认，请稍后重试。", 503)
        _observe("send", purpose, "accepted")
        return {
            "challenge_id": challenge_id,
            "expires_in": settings.sms_code_ttl_seconds,
            "resend_after": settings.sms_phone_interval_seconds,
        }
    await storage.finish_sms_send(
        challenge_id, "uncertain" if result.kind == "timeout" else "failed", now,
    )
    _observe("send", purpose, result.kind)
    if result.kind == "timeout":
        raise SmsRequestError("短信发送超时，请稍后再试。", 503)
    if _vendor_limited(result.code):
        wait = settings.sms_phone_interval_seconds
        raise SmsRequestError(f"发送过于频繁，请在 {wait} 秒后重试。", 429, wait)
    raise SmsRequestError("短信服务暂不可用，请稍后重试。", 503)


async def _claim(
    storage, provider, settings, *, phone: str, purpose: str, challenge_id: str, code: str,
    user_id: str | None, clock: Callable[[], datetime],
) -> tuple[dict, str, datetime]:
    _require_sms(settings, purpose)
    number = _phone(phone)
    verification_code = _verification_code(code)
    digest = phone_digest(number.e164, settings.phone_hmac_key)
    challenge = _challenge(challenge_id)
    claim = await storage.claim_sms_attempt(
        challenge_id=challenge, purpose=purpose, phone_hmac=digest,
        user_id=user_id, max_attempts=settings.sms_max_attempts, now=clock(),
    )
    if claim is None:
        raise SmsRequestError("验证码无效或已过期。")
    result = await _call(provider, "check", SmsCheckRequest(
        phone_number=number.national, country_code="86", scheme_name=claim["scheme_name"],
        out_id=claim["out_id"], verify_code=verification_code, case_auth_policy=1,
    ))
    checked_at = clock()
    _observe("check", purpose, result.kind)
    if result.kind == "pass":
        return claim, digest, checked_at
    close = result.kind in {"timeout", "incomplete"}
    exhausted = claim["attempts"] >= settings.sms_max_attempts
    status = "uncertain" if close else ("failed" if exhausted else "sent")
    await storage.settle_sms_attempt(challenge, claim["generation"], status, checked_at)
    if close:
        raise SmsRequestError("验证码核验结果不确定，请重新获取验证码。", 503)
    if exhausted:
        raise SmsRequestError("验证码已失效，请重新获取。")
    raise SmsRequestError("验证码不正确。")


async def _commit(storage, challenge_id: str, claim, now: datetime, action) -> None:
    try:
        await action()
    except AccountConflict:
        await storage.settle_sms_attempt(challenge_id, claim["generation"], "sent", now)
        raise
    except SmsIdentityConflict as exc:
        status = "sent" if exc.retryable else "failed"
        await storage.settle_sms_attempt(challenge_id, claim["generation"], status, now)
        raise SmsRequestError(str(exc)) from None
    except ChallengeLost:
        raise SmsRequestError("验证码已失效，请重新获取。") from None


async def register_with_phone(
    storage, provider, settings, *, user_id: str, password: str, confirm_password: str,
    phone: str, challenge_id: str, code: str, clock: Callable[[], datetime],
) -> str:
    if password != confirm_password:
        raise SmsRequestError("两次输入的密码不一致。")
    if user_id == "admin":
        raise SmsRequestError("不能使用该用户 ID。")
    claim, digest, verified_at = await _claim(
        storage, provider, settings, phone=phone, purpose="register", challenge_id=challenge_id,
        code=code, user_id=None, clock=clock,
    )
    encoded = await asyncio.to_thread(hash_password, password)
    number = _phone(phone)

    async def create() -> None:
        await storage.register_verified_user(
            challenge_id=challenge_id, generation=claim["generation"], user_id=user_id,
            display_name=user_id, password_hash=encoded, phone_hmac=digest,
            phone_tail=number.tail, verified_at=verified_at, now=clock(),
        )

    await _commit(storage, challenge_id, claim, verified_at, create)
    return user_id


async def login_with_phone(
    storage, provider, settings, *, phone: str, challenge_id: str, code: str,
    clock: Callable[[], datetime],
) -> str:
    claim, digest, _ = await _claim(
        storage, provider, settings, phone=phone, purpose="login", challenge_id=challenge_id,
        code=code, user_id=None, clock=clock,
    )
    try:
        outcome = await storage.login_verified_phone(
            challenge_id=challenge_id, generation=claim["generation"], phone_hmac=digest, now=clock(),
        )
    except ChallengeLost:
        raise SmsRequestError("验证码已失效，请重新获取。") from None
    if outcome["kind"] == "unregistered":
        raise SmsRequestError("该手机号尚未注册，请先注册账号。")
    if outcome["kind"] != "user":
        raise SmsRequestError("该手机号无法用于登录。")
    return str(outcome["user_id"])


async def bind_phone(
    storage, provider, settings, *, user_id: str, password: str, phone: str,
    challenge_id: str, code: str, clock: Callable[[], datetime],
) -> dict:
    _require_sms(settings, "bind")
    if user_id == "admin":
        raise SmsRequestError("内置管理员请继续使用密码登录。", 403)
    if await storage.phone_tail(user_id):
        raise SmsRequestError("当前账号已绑定手机号。")
    encoded = await storage.account_password(user_id)
    if not await asyncio.to_thread(verify_password, password, encoded):
        raise SmsRequestError("密码错误。")
    claim, digest, verified_at = await _claim(
        storage, provider, settings, phone=phone, purpose="bind", challenge_id=challenge_id,
        code=code, user_id=user_id, clock=clock,
    )
    number = _phone(phone)

    async def save() -> None:
        await storage.bind_verified_phone(
            challenge_id=challenge_id, generation=claim["generation"], user_id=user_id,
            expected_password_hash=encoded, phone_hmac=digest, phone_tail=number.tail, now=clock(),
        )

    await _commit(storage, challenge_id, claim, verified_at, save)
    return {"ok": True, "phone_tail": number.tail}


async def reset_password_with_phone(
    storage, provider, settings, *, phone: str, challenge_id: str, code: str,
    password: str, confirm_password: str, clock: Callable[[], datetime],
) -> str:
    if password != confirm_password:
        raise SmsRequestError("两次输入的密码不一致。")
    if not 5 <= len(password) <= 128:
        raise SmsRequestError("密码需要 5～128 个字符。")
    claim, digest, verified_at = await _claim(
        storage, provider, settings, phone=phone, purpose="reset", challenge_id=challenge_id,
        code=code, user_id=None, clock=clock,
    )
    encoded = await asyncio.to_thread(hash_password, password)
    outcome: dict = {}

    async def reset() -> None:
        nonlocal outcome
        outcome = await storage.reset_password_verified_phone(
            challenge_id=challenge_id, generation=claim["generation"],
            phone_hmac=digest, password_hash=encoded, now=clock(),
        )

    await _commit(storage, challenge_id, claim, verified_at, reset)
    if outcome.get("kind") == "unregistered":
        raise SmsRequestError("该手机号尚未绑定账号。")
    if outcome.get("kind") != "reset":
        raise SmsRequestError("该手机号无法用于找回密码。")
    return str(outcome["user_id"])
