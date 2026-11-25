"""用替身命令验证发版顺序和失败边界，不连接服务器。"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "deploy.sh"


@pytest.mark.parametrize("failure", ["", "pull", "build", "readiness", "jwt-missing", "jwt-short", "jwt-ttl"])
def test_deploy_order_and_failure_boundary(tmp_path, failure):
    if os.geteuid() == 0:
        pytest.skip("部署脚本要求非 root 账号")
    project = tmp_path / "project"
    (project / "ops").mkdir(parents=True)
    (project / ".git").mkdir()
    config = tmp_path / "config.env"
    secret = "z" * 32
    jwt_config = {
        "jwt-missing": "",
        "jwt-short": "MELONCLAW_JWT_SECRET=short\n",
        "jwt-ttl": f"MELONCLAW_JWT_SECRET={secret}\nMELONCLAW_TOKEN_TTL_HOURS=0\n",
    }
    config.write_text(jwt_config.get(failure, f"MELONCLAW_JWT_SECRET={secret}\n"))
    # 在临时目录使用当前测试环境的 Python 和应用配置解析。
    script = project / "ops" / "deploy.sh"
    script.write_text(
        SCRIPT.read_text()
        .replace("/etc/melonclaw/melonclaw.env", str(config))
        .replace(".venv/bin/python", shlex.quote(sys.executable))
    )
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "commands.log"
    shim = '''#!/usr/bin/env python3
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["DEPLOY_TEST_LOG"], "a") as stream:
    stream.write(name + " " + " ".join(args) + "\\n")
failure = os.environ["DEPLOY_TEST_FAILURE"]
if name == "git" and args == ["rev-parse", "--git-dir"]:
    print(".git")
if name == "git" and args == ["pull", "--ff-only"] and failure == "pull":
    sys.exit(1)
if name == "npm" and "build" in args and failure == "build":
    sys.exit(1)
if name == "curl":
    print(json.dumps({"ready": failure != "readiness"}))
'''
    for name in ["git", "uv", "npm", "sudo", "systemctl", "curl", "flock", "sleep"]:
        executable = commands / name
        executable.write_text(shim)
        executable.chmod(0o755)
    env = dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}",
               PYTHONPATH=str(SCRIPT.parents[1] / "src"),
               MELONCLAW_JWT_SECRET=secret, MELONCLAW_TOKEN_TTL_HOURS="24",
               DEPLOY_TEST_LOG=str(log), DEPLOY_TEST_FAILURE=failure)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=30)
    calls = log.read_text().splitlines()
    if failure.startswith("jwt-"):
        setting = "MELONCLAW_TOKEN_TTL_HOURS" if failure == "jwt-ttl" else "MELONCLAW_JWT_SECRET"
        assert result.returncode == 1
        assert setting in result.stderr
        assert secret not in result.stdout + result.stderr
        assert not any(call.startswith(("sudo ", "systemctl ")) for call in calls)
        return

    stop = calls.index("sudo systemctl stop melonclaw")
    pull = calls.index("git pull --ff-only")
    assert stop < pull
    assert not any("db-init" in line or "db-update" in line for line in calls)
    if failure:
        assert result.returncode != 0
        assert calls[-1] == "sudo systemctl stop melonclaw"
        if failure in {"pull", "build"}:
            assert "sudo systemctl start melonclaw" not in calls
        if failure == "pull":
            assert not any(line.startswith("npm ") for line in calls)
    else:
        assert result.returncode == 0, result.stderr
        assert pull < calls.index("npm --prefix frontend ci")
        assert calls.index("npm --prefix frontend run build") < calls.index("sudo systemctl start melonclaw")
        assert "systemctl is-active --quiet melonclaw" in calls
        assert not any("task-worker" in call for call in calls)
        assert "发版成功" in result.stdout


def test_web_unit_hosts_scheduler_without_separate_worker():
    value = (SCRIPT.parent / "systemd" / "melonclaw.service").read_text()
    assert "Environment=MELONCLAW_ENV_FILE=/etc/melonclaw/melonclaw.env" in value
    assert "db-init" not in value and "db-update" not in value
    assert not (SCRIPT.parent / "systemd" / "melonclaw-task-worker.service").exists()
