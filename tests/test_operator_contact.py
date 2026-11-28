"""公开运营主体和申请邮箱。不发送邮件。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_account_api import MemoryAccounts

from melonclaw.api.app import create_app
from melonclaw.api.audit import RETENTION_DAYS
from melonclaw.core.config import load_settings
from melonclaw.repository.sms_auth import SMS_RECORD_CLEANUP_HOURS

ROOT = Path(__file__).resolve().parents[1]


def test_operator_name_and_report_email_stay_independent(monkeypatch):
    monkeypatch.delenv("MELONCLAW_OPERATOR_NAME", raising=False)
    monkeypatch.delenv("MELONCLAW_REPORT_CONTACT_EMAIL", raising=False)
    settings = load_settings()
    assert settings.operator_name == ""
    assert settings.report_contact_email == ""
    monkeypatch.setenv("MELONCLAW_REPORT_CONTACT_EMAIL", "not-an-email")
    assert load_settings().report_contact_email == ""
    monkeypatch.setenv("MELONCLAW_OPERATOR_NAME", "测试\n主体")
    with pytest.raises(RuntimeError, match="MELONCLAW_OPERATOR_NAME"):
        load_settings()
    monkeypatch.setenv("MELONCLAW_OPERATOR_NAME", "测试主体")
    monkeypatch.setenv("MELONCLAW_REPORT_CONTACT_EMAIL", "privacy-contact@example.test")
    settings = load_settings()
    assert settings.operator_name == "测试主体"
    assert settings.report_contact_email == "privacy-contact@example.test"


def test_auth_config_publishes_only_the_public_contact(monkeypatch):
    monkeypatch.setenv("profile", "")
    monkeypatch.setenv("MELONCLAW_OPERATOR_NAME", "测试主体")
    monkeypatch.setenv("MELONCLAW_REPORT_CONTACT_EMAIL", "privacy-contact@example.test")

    async def run():
        app = create_app()
        app.state.chat = SimpleNamespace(storage=MemoryAccounts())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/auth/config")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            body = response.json()
            assert body["operator_name"] == "测试主体"
            assert body["operator_email"] == "privacy-contact@example.test"
            assert "sms_access_key_id" not in body
            assert "phone_hmac_key" not in body

    asyncio.run(run())


def test_privacy_page_states_implemented_retention_without_a_fake_mailbox():
    page = (ROOT / "frontend/src/components/PrivacyPage.tsx").read_text(encoding="utf-8")
    assert f"{SMS_RECORD_CLEANUP_HOURS} 小时" in page
    assert f"{RETENTION_DAYS} 天" in page
    assert "5 分钟" in page and "1 分钟" in page and "30 分钟" in page
    assert "24 小时" in page and "30 天" in page
    assert "已提交" not in page and "已受理" not in page
    assert "@" not in page
