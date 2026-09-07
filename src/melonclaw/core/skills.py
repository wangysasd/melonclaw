"""项目 Skill 的来源与统一的 Agent 文件后端。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.backends.protocol import BackendProtocol


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_SKILLS_DIR = PROJECT_ROOT / "skills"
SKILLS_ROUTE = "/skills/"


def build_agent_backend(
    workspace_dir: Path,
) -> tuple[BackendProtocol, list[str], list[FilesystemPermission]]:
    """构造指定工作区，并把虚拟 ``/skills/`` 路由到项目源目录。

    普通文件操作和 Shell 命令使用调用方提供的工作区；CLI 传入进程临时
    runtime，Web 则传入持久 Project workdir。项目 Skill 通过
    ``CompositeBackend`` 的独立路由直接读取仓库根目录下的 ``skills/``，不在
    工作区中创建副本。DeepAgents 0.7 要求传入已经构造好的 Backend 实例，但
    不影响使用 ``CompositeBackend`` 做路径路由。

    ``LocalShellBackend`` 的 Shell 能力没有沙箱隔离，Web 入口因此只适合本机
    开发，并默认只监听 127.0.0.1。文件写入和 Shell 执行仍由 HITL 保护。
    """

    workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime_backend: BackendProtocol = LocalShellBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
        env={
            "PATH": os.pathsep.join(
                [
                    str(Path(sys.executable).parent),
                    os.environ.get("PATH", "/usr/bin:/bin"),
                ]
            )
        },
    )

    if not PROJECT_SKILLS_DIR.is_dir():
        return runtime_backend, [], []

    skills_backend = FilesystemBackend(
        root_dir=PROJECT_SKILLS_DIR,
        virtual_mode=True,
    )
    backend = CompositeBackend(
        default=runtime_backend,
        routes={SKILLS_ROUTE: skills_backend},
    )
    permissions = [
        FilesystemPermission(
            operations=["write"],
            paths=[f"{SKILLS_ROUTE}**"],
            mode="deny",
        ),
    ]
    return backend, [SKILLS_ROUTE], permissions


def project_skills_enabled() -> bool:
    """返回当前项目是否存在可供 Agent 发现的 Skill。"""

    return PROJECT_SKILLS_DIR.is_dir()
