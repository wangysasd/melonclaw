"""真实 Shell 子进程验证环境注入和快照隔离；仅使用合成变量。"""

import asyncio
import os
import shlex
import sys

from melonclaw.backend.factory import build_agent_backend


def python_command(code):
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def test_shell_receives_only_base_and_explicit_business_values(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_TOKEN", "deployment-fixture")
    monkeypatch.setenv("TUSHARE_MCP_TOKEN", "deployment-fixture")
    monkeypatch.setenv("DATABASE_URL", "database-fixture")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "tracing-fixture")
    monkeypatch.setenv("MELONCLAW_SHELL_ENV_ALLOWLIST", "*")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_TIME", "C")
    monkeypatch.setenv("TZ", "UTC")
    backend, _, _ = build_agent_backend(
        tmp_path,
        environment={
            "APP_TOKEN": "database-fixture",
            "EMPTY": "",
            "HOME": "/invalid-fixture",
            "PATH": "/invalid-fixture",
            "PYTHONPATH": "/invalid-fixture",
            "BASH_ENV": "/invalid-fixture",
        },
    )
    command = python_command(
        "import os; "
        "assert os.environ['APP_TOKEN'] == 'database-fixture'; "
        "assert os.environ['EMPTY'] == ''; "
        "assert os.environ['LANG'] == os.environ['LC_TIME'] == 'C'; "
        "assert os.environ['TZ'] == 'UTC'; "
        "assert os.environ['HOME'] != '/invalid-fixture'; "
        "assert os.environ['PATH'] != '/invalid-fixture'; "
        "assert not {'TUSHARE_MCP_TOKEN', 'DATABASE_URL', 'LANGFUSE_SECRET_KEY', "
        "'MELONCLAW_SHELL_ENV_ALLOWLIST', 'PYTHONPATH', 'BASH_ENV'}.intersection(os.environ); "
        "print('verified')"
    )
    response = asyncio.run(backend.aexecute(command))
    assert response.exit_code == 0 and response.output.strip() == "verified"
    assert os.environ["APP_TOKEN"] == "deployment-fixture"


def test_running_backend_retains_snapshot_while_new_backend_uses_new_values(tmp_path):
    values = {"APP_TOKEN": "first-fixture"}
    first, _, _ = build_agent_backend(tmp_path / "first", environment=values)
    values["APP_TOKEN"] = "second-fixture"
    second, _, _ = build_agent_backend(tmp_path / "second", environment=values)

    async def run():
        for backend, expected in ((first, "first-fixture"), (second, "second-fixture")):
            response = await backend.aexecute(
                python_command(
                    f"import os; assert os.environ['APP_TOKEN'] == {expected!r}; print('verified')"
                )
            )
            assert response.exit_code == 0 and response.output.strip() == "verified"

    asyncio.run(run())
