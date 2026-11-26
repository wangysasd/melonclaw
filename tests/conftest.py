"""测试进程默认具备启动所需的签名密钥，不写入仓库配置。"""

import os

import pytest

os.environ.setdefault("MELONCLAW_JWT_SECRET", "pytest-jwt-secret-must-be-32-characters")


@pytest.fixture(autouse=True)
def isolate_security_audit(tmp_path, monkeypatch):
    """写操作审计落到临时目录，避免测试写入本机数据根。"""

    monkeypatch.setenv("MELONCLAW_AUDIT_DIR", str(tmp_path / "security-audit"))
