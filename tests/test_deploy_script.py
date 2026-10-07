"""用替身命令验证发版顺序和失败边界，不连接服务器。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "deploy.sh"


@pytest.mark.parametrize("failure", ["", "pull", "build", "readiness"])
def test_deploy_order_and_failure_boundary(tmp_path, failure):
    if os.geteuid() == 0:
        pytest.skip("部署脚本要求非 root 账号")
    project = tmp_path / "project"
    (project / "ops").mkdir(parents=True)
    (project / ".git").mkdir()
    config = tmp_path / "config.env"
    config.touch()
    # 仅替换外部配置位置；测试环境不写 /etc。
    script = project / "ops" / "deploy.sh"
    script.write_text(SCRIPT.read_text().replace("/etc/melonclaw/melonclaw.env", str(config)))
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
               DEPLOY_TEST_LOG=str(log), DEPLOY_TEST_FAILURE=failure)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True,
                            timeout=30)
    calls = log.read_text().splitlines()
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
        assert "发版成功" in result.stdout
