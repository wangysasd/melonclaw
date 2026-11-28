"""号码认证服务的短信认证客户端。网络失败不自动重发。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

_TIMEOUT_NAMES = ("timeout", "timedout")


@dataclass(frozen=True)
class SmsSendRequest:
    phone_number: str
    country_code: str
    sign_name: str
    template_code: str
    template_param: str
    scheme_name: str
    out_id: str
    code_length: int
    valid_time: int
    interval: int
    code_type: int
    return_verify_code: bool
    duplicate_policy: int
    auto_retry: int


@dataclass(frozen=True)
class SmsCheckRequest:
    phone_number: str
    country_code: str
    scheme_name: str
    out_id: str
    verify_code: str
    case_auth_policy: int


@dataclass(frozen=True)
class SmsCallResult:
    kind: str
    code: str = ""


def interpret_send(body) -> SmsCallResult:
    code = str(getattr(body, "code", "") or "")
    success = getattr(body, "success", None)
    if code == "OK" and success is not False:
        return SmsCallResult("sent")
    if not code:
        return SmsCallResult("incomplete")
    return SmsCallResult("error", code=code[:80])


def interpret_check(body) -> SmsCallResult:
    """只有调用成功且 VerifyResult 为 PASS 才算通过。UNKNOWN 是核验失败。"""

    code = str(getattr(body, "code", "") or "")
    success = getattr(body, "success", None)
    if not code or success is None:
        return SmsCallResult("incomplete")
    if code != "OK" or success is not True:
        return SmsCallResult("error", code=(code or "ERROR")[:80])
    result = str(getattr(getattr(body, "model", None), "verify_result", "") or "")
    if result == "PASS":
        return SmsCallResult("pass")
    if result == "UNKNOWN":
        return SmsCallResult("unknown")
    return SmsCallResult("incomplete")


def interpret_exception(exc: BaseException) -> SmsCallResult:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or any(
            name in type(current).__name__.lower() for name in _TIMEOUT_NAMES
        ):
            return SmsCallResult("timeout")
        current = current.__cause__ or current.__context__
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return SmsCallResult("error", code=code[:80])
    return SmsCallResult("error", code="PROVIDER")


class AliyunSmsProvider:
    """调用 SendSmsVerifyCode 与 CheckSmsVerifyCode。不记录请求或响应正文。"""

    def __init__(self, settings):
        self._settings = settings
        self._client = None

    async def send(self, request: SmsSendRequest) -> SmsCallResult:
        return await self._invoke("send_sms_verify_code_with_options", self._send_model(request), interpret_send)

    async def check(self, request: SmsCheckRequest) -> SmsCallResult:
        return await self._invoke(
            "check_sms_verify_code_with_options", self._check_model(request), interpret_check,
        )

    async def _invoke(self, method: str, model, interpret) -> SmsCallResult:
        timeout = self._settings.sms_timeout_seconds
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._call, method, model), timeout + 1,
            )
        except TimeoutError:
            return SmsCallResult("timeout")
        except Exception as exc:
            return interpret_exception(exc)
        return interpret(getattr(response, "body", None))

    def _call(self, method: str, model):
        from alibabacloud_tea_util import models as util_models

        runtime = util_models.RuntimeOptions(
            connect_timeout=3000,
            read_timeout=self._settings.sms_timeout_seconds * 1000,
            autoretry=False,
            max_attempts=1,
        )
        return getattr(self._sdk(), method)(model, runtime)

    def _sdk(self):
        if self._client is None:
            from alibabacloud_dypnsapi20170525.client import Client
            from alibabacloud_tea_openapi import models as open_api_models

            config = open_api_models.Config(
                access_key_id=self._settings.sms_access_key_id,
                access_key_secret=self._settings.sms_access_key_secret,
                endpoint="dypnsapi.aliyuncs.com",
            )
            config.connect_timeout = 3000
            config.read_timeout = self._settings.sms_timeout_seconds * 1000
            self._client = Client(config)
        return self._client

    def _send_model(self, request: SmsSendRequest):
        from alibabacloud_dypnsapi20170525 import models

        return models.SendSmsVerifyCodeRequest(
            phone_number=request.phone_number,
            country_code=request.country_code,
            sign_name=request.sign_name,
            template_code=request.template_code,
            template_param=request.template_param,
            scheme_name=request.scheme_name,
            out_id=request.out_id,
            code_length=request.code_length,
            valid_time=request.valid_time,
            interval=request.interval,
            code_type=request.code_type,
            return_verify_code=request.return_verify_code,
            duplicate_policy=request.duplicate_policy,
            auto_retry=request.auto_retry,
        )

    def _check_model(self, request: SmsCheckRequest):
        from alibabacloud_dypnsapi20170525 import models

        return models.CheckSmsVerifyCodeRequest(
            phone_number=request.phone_number,
            country_code=request.country_code,
            scheme_name=request.scheme_name,
            out_id=request.out_id,
            verify_code=request.verify_code,
            case_auth_policy=request.case_auth_policy,
        )
