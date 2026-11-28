"""测试进程默认具备启动所需的签名密钥，不写入仓库配置。"""

import os

import pytest

os.environ.setdefault("MELONCLAW_JWT_SECRET", "pytest-jwt-secret-must-be-32-characters")


@pytest.fixture(autouse=True)
def isolate_security_audit(tmp_path, monkeypatch):
    """写操作审计落到临时目录，避免测试写入本机数据根。"""

    monkeypatch.setenv("MELONCLAW_AUDIT_DIR", str(tmp_path / "security-audit"))
    monkeypatch.delenv("MELONCLAW_AUDIT_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("MELONCLAW_AUDIT_ALERT_RETRY_SECONDS", "300")
    for name in list(os.environ):
        if name.startswith("MELONCLAW_SMS_") or name in {
            "MELONCLAW_REGISTRATION_ENABLED",
            "MELONCLAW_PHONE_HMAC_KEY",
        }:
            monkeypatch.delenv(name, raising=False)
    from melonclaw.api.audit_alert import reset_audit_alerts

    reset_audit_alerts()
