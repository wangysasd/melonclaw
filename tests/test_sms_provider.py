"""号码认证响应解释。不连接阿里云。"""

from types import SimpleNamespace

from sms_fakes import sms_settings

from melonclaw.services.sms_provider import (
    AliyunSmsProvider,
    SmsCallResult,
    SmsCheckRequest,
    SmsSendRequest,
    interpret_check,
    interpret_exception,
    interpret_send,
)


def _send(**overrides):
    values = {
        "phone_number": "13800138000", "country_code": "86", "sign_name": "签名",
        "template_code": "100001", "template_param": '{"code":"##code##","min":"5"}',
        "scheme_name": "prod-register", "out_id": "abc", "code_length": 6, "valid_time": 300,
        "interval": 60, "code_type": 1, "return_verify_code": False, "duplicate_policy": 1,
        "auto_retry": 0,
    }
    values.update(overrides)
    return SmsSendRequest(**values)


def test_success_discards_plaintext_code_and_unknown_is_not_a_pass():
    sent = interpret_send(SimpleNamespace(
        code="OK", success=True, model=SimpleNamespace(verify_code="654321"),
    ))
    assert sent == SmsCallResult("sent")
    assert "654321" not in repr(sent)
    unknown = interpret_check(SimpleNamespace(
        code="OK", success=True, model=SimpleNamespace(verify_result="UNKNOWN"),
    ))
    assert unknown.kind == "unknown"
    assert interpret_check(SimpleNamespace(
        code="OK", success=True, model=SimpleNamespace(verify_result="PASS"),
    )).kind == "pass"
    assert interpret_check(SimpleNamespace(code="OK", success=True, model=None)).kind == "incomplete"
    pass_model = SimpleNamespace(verify_result="PASS")
    assert interpret_check(SimpleNamespace(code="OK", model=pass_model)).kind == "incomplete"
    assert interpret_check(SimpleNamespace(success=True, model=pass_model)).kind == "incomplete"
    assert interpret_check(SimpleNamespace(code="OK", success=False, model=pass_model)).kind == "error"
    assert interpret_send(SimpleNamespace(
        code="BUSINESS_LIMIT_CONTROL", success=False, model=None,
    )).code == "BUSINESS_LIMIT_CONTROL"


def test_timeout_exception_is_not_retried_as_success():
    class ReadTimeoutError(TimeoutError):
        pass

    assert interpret_exception(ReadTimeoutError()).kind == "timeout"
    assert interpret_exception(RuntimeError("secret phone 13800138000")).code == "PROVIDER"
    assert "13800138000" not in repr(interpret_exception(RuntimeError("13800138000")))


def test_sdk_request_uses_matching_scheme_and_does_not_ask_for_the_code():
    provider = AliyunSmsProvider(sms_settings())
    request = _send()
    model = provider._send_model(request)
    assert model.return_verify_code is False
    assert model.auto_retry == 0
    assert model.duplicate_policy == 1
    assert model.code_length == 6
    assert model.code_type == 1
    assert model.country_code == "86"
    assert "##code##" in model.template_param
    check = provider._check_model(SmsCheckRequest(
        phone_number=request.phone_number, country_code="86", scheme_name=request.scheme_name,
        out_id=request.out_id, verify_code="123456", case_auth_policy=1,
    ))
    assert check.scheme_name == model.scheme_name
    assert check.out_id == model.out_id
    assert check.phone_number == model.phone_number
