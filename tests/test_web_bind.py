"""Web 监听地址只从配置入口解析。"""

import pytest

from melonclaw.api.app import main
from melonclaw.core.config import web_bind


def test_web_bind_defaults(monkeypatch):
    monkeypatch.delenv("MELONCLAW_HOST", raising=False)
    monkeypatch.delenv("MELONCLAW_PORT", raising=False)
    assert web_bind() == ("127.0.0.1", 8000)


def test_web_bind_reads_configured_address(monkeypatch):
    monkeypatch.setenv("MELONCLAW_HOST", "0.0.0.0")
    monkeypatch.setenv("MELONCLAW_PORT", "8123")
    assert web_bind() == ("0.0.0.0", 8123)


def test_web_bind_rejects_non_integer_port(monkeypatch):
    monkeypatch.setenv("MELONCLAW_PORT", "abc")
    with pytest.raises(RuntimeError, match="MELONCLAW_PORT"):
        web_bind()


def test_web_entry_uses_config_bind(monkeypatch):
    monkeypatch.setenv("MELONCLAW_HOST", "127.0.0.1")
    monkeypatch.setenv("MELONCLAW_PORT", "8123")
    seen = {}

    def run(_app, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("uvicorn.run", run)
    main()
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8123
    assert seen["log_level"] == "info"
