"""项目 Skill 来源、Memory 路由与 Agent 文件后端的组装。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from deepagents import FilesystemPermission
from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    LocalShellBackend,
    StoreBackend,
)
from deepagents.backends.protocol import BackendProtocol
from langgraph.store.base import BaseStore

from melonclaw.core.defaults import resolve_agent_env
from melonclaw.memory.service import namespace_for_context

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_SKILLS_DIR = PROJECT_ROOT / "skills"
SKILLS_ROUTE = "/skills/"


def _build_shell_env() -> dict[str, str]:
    """构造 Shell 子进程环境：PATH 加代码默认层解析出的变量。

    变量名与合并逻辑见 ``core/defaults.py``：代码默认凭据名取值于
    部署者 ``.env``，平台自身凭据被硬黑名单剔除。未来多租户时在
    ``resolve_agent_env`` 的 ``overrides`` 参数接入用户级配置。
    """

    env = resolve_agent_env()
    env["PATH"] = os.pathsep.join(
        [
            str(Path(sys.executable).parent),
            os.environ.get("PATH", "/usr/bin:/bin"),
        ]
    )
    return env


def build_agent_backend(
    workspace_dir: Path,
    *,
    default_backend: BackendProtocol | None = None,
    memory_store: BaseStore | None = None,
    installation_id: str = "local",
    agent_id: str = "quickstart-research-agent",
) -> tuple[BackendProtocol, list[str], list[FilesystemPermission]]:
    """构造指定工作区，并把虚拟 ``/skills/`` 路由到项目源目录。

    普通文件操作和 Shell 命令使用调用方提供的持久 Project workdir。项目 Skill 通过
    ``CompositeBackend`` 的独立路由直接读取仓库根目录下的 ``skills/``，不在
    工作区中创建副本。DeepAgents 0.7 要求传入已经构造好的 Backend 实例，但
    不影响使用 ``CompositeBackend`` 做路径路由。

    ``LocalShellBackend`` 的 Shell 能力没有沙箱隔离，Web 入口因此只适合本机
    开发，并默认只监听 127.0.0.1。文件写入和 Shell 执行仍由 HITL 保护。
    Shell 子进程环境由 ``_build_shell_env()`` 按代码默认层构造，Skill 脚本
    可读取 ``core/defaults.py`` 默认集合中声明的凭据变量（值来自 ``.env``），
    模型与数据库凭据不透传。
    """

    workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime_backend: BackendProtocol = default_backend or LocalShellBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
        env=_build_shell_env(),
    )

    routes: dict[str, BackendProtocol] = {}
    permissions: list[FilesystemPermission] = []

    if memory_store is not None:
        routes.update(
            {
                "/memories/global/": StoreBackend(
                    store=memory_store,
                    namespace=lambda runtime: namespace_for_context(
                        runtime.context,
                        "global",
                        installation_id=installation_id,
                        agent_id=agent_id,
                    ),
                ),
                "/memories/tenant/": StoreBackend(
                    store=memory_store,
                    namespace=lambda runtime: namespace_for_context(
                        runtime.context,
                        "tenant",
                        installation_id=installation_id,
                        agent_id=agent_id,
                    ),
                ),
                "/memories/user/": StoreBackend(
                    store=memory_store,
                    namespace=lambda runtime: namespace_for_context(
                        runtime.context,
                        "user",
                        installation_id=installation_id,
                        agent_id=agent_id,
                    ),
                ),
            }
        )
        # 长期 Memory 的写入必须经过 MemoryService 的固定工具、锁和审计。
        permissions.append(
            FilesystemPermission(
                operations=["write"],
                paths=[
                    "/memories/global/**",
                    "/memories/tenant/**",
                    "/memories/user/**",
                ],
                mode="deny",
            )
        )

    if PROJECT_SKILLS_DIR.is_dir():
        routes[SKILLS_ROUTE] = FilesystemBackend(
            root_dir=PROJECT_SKILLS_DIR,
            virtual_mode=True,
        )
        permissions.append(
            FilesystemPermission(
                operations=["write"],
                paths=[f"{SKILLS_ROUTE}**"],
                mode="deny",
            )
        )

    if not routes:
        return runtime_backend, [], permissions

    backend = CompositeBackend(
        default=runtime_backend,
        routes=routes,
    )
    return backend, ([SKILLS_ROUTE] if PROJECT_SKILLS_DIR.is_dir() else []), permissions


def project_skills_enabled() -> bool:
    """返回当前项目是否存在可供 Agent 发现的 Skill。"""

    return PROJECT_SKILLS_DIR.is_dir()
