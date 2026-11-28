"""项目 Skill 来源、Memory 路由与 Agent 文件后端的组装。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

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


def _build_shell_env(environment: dict[str, str] | None = None) -> dict[str, str]:
    """只合并系统基础环境与服务层已解析的业务变量。"""
    env = resolve_agent_env(overrides=environment)
    env["PATH"] = os.pathsep.join(
        [
            str(Path(sys.executable).parent),
            os.environ.get("PATH", "/usr/bin:/bin"),
        ]
    )
    return env


class _VirtualRouteBackend(BackendProtocol):
    """仅通过虚拟文件工具访问的本地目录路由包装。

    不继承 ``FilesystemBackend``，避免 ``FilesystemMiddleware`` 在系统提示中
    为 ``/.attachments/``、``/.artifacts/`` 输出包含会话 UUID 的宿主机映射路径：
    既防止向 Shell 提示暴露内部目录宿主机路径，又保持不同新会话的系统提示前缀
    完全一致，使模型 KV 前缀缓存跨会话生效。
    """

    def __init__(self, root_dir: Path) -> None:
        self._inner = FilesystemBackend(root_dir=root_dir, virtual_mode=True)

    def ls(self, path: str) -> Any:
        return self._inner.ls(path)

    async def als(self, path: str) -> Any:
        return await self._inner.als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        return self._inner.read(file_path, offset=offset, limit=limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        return await self._inner.aread(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> Any:
        return self._inner.write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> Any:
        return await self._inner.awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        return self._inner.edit(file_path, old_string, new_string, replace_all=replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> Any:
        return await self._inner.aedit(file_path, old_string, new_string, replace_all=replace_all)

    def delete(self, file_path: str) -> Any:
        return self._inner.delete(file_path)

    async def adelete(self, file_path: str) -> Any:
        return await self._inner.adelete(file_path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        return self._inner.grep(pattern, path=path, glob=glob)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        return await self._inner.agrep(pattern, path=path, glob=glob)

    def glob(self, pattern: str, path: str = "/") -> Any:
        return self._inner.glob(pattern, path=path)

    async def aglob(self, pattern: str, path: str = "/") -> Any:
        return await self._inner.aglob(pattern, path=path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return self._inner.upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return await self._inner.aupload_files(files)

    def download_files(self, paths: list[str]) -> Any:
        return self._inner.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> Any:
        return await self._inner.adownload_files(paths)


def build_agent_backend(
    workspace_dir: Path,
    *,
    default_backend: BackendProtocol | None = None,
    memory_store: BaseStore | None = None,
    installation_id: str = "local",
    agent_id: str = "quickstart-research-agent",
    skill_dirs: tuple[tuple[str, Path], ...] = (),
    environment: dict[str, str] | None = None,
) -> tuple[BackendProtocol, list[str], list[FilesystemPermission]]:
    """构造指定工作区，并把虚拟 Skill 路由挂到调用方给出的目录。

    普通文件操作和 Shell 命令使用调用方提供的持久工作区。Skill 通过
    ``CompositeBackend`` 的独立路由只读挂载，不在工作区中创建副本；挂哪些
    根目录（共享 /skills/、用户 /skills-user/）由调用方按当前用户可见性决定。
    DeepAgents 0.7 要求传入已经构造好的 Backend 实例，但不影响使用
    ``CompositeBackend`` 做路径路由。

    ``LocalShellBackend`` 的 Shell 能力没有沙箱隔离，Web 入口因此只适合本机
    开发，并默认只监听 127.0.0.1。文件工具与 Shell 免逐次审批，文件工具仍受
    下方的路径与权限规则约束；这些规则不能把 Shell 变成安全沙箱。
    Shell 环境由基础变量和服务层解析的数据库变量显式注入，不继承业务进程环境。
    """

    workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime_backend: BackendProtocol = default_backend or LocalShellBackend(
        root_dir=workspace_dir,
        virtual_mode=True,
        env=_build_shell_env(environment),
    )

    routes: dict[str, BackendProtocol] = {}
    # Deep Agents 0.7 不支持对带 execute 能力的默认 backend 应用权限规则，
    # 但允许权限规则全部落在 CompositeBackend 的非执行路由上。附件和
    # artifacts 不能让 Agent 通过 Shell 绕过文件工具权限，因此单独使用
    # 不提供 execute 的 FilesystemBackend；Shell 仍固定委托给默认 backend。
    attachments_dir = workspace_dir / ".attachments"
    artifacts_dir = workspace_dir / ".artifacts"
    attachments_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    routes.update(
        {
            "/.attachments/": _VirtualRouteBackend(attachments_dir),
            "/.artifacts/": _VirtualRouteBackend(artifacts_dir),
        }
    )
    permissions: list[FilesystemPermission] = [
        # 附件由服务层写入和删除，Agent 工具只能看到派生目录。
        FilesystemPermission(
            operations=["write"],
            paths=["/.attachments/**"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/.attachments/**/original/**"],
            mode="deny",
        ),
        # 摘要中间件通过 backend API 写入，不经过工具权限检查。
        FilesystemPermission(
            operations=["write"],
            paths=["/.artifacts/**"],
            mode="deny",
        ),
    ]

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

    skill_sources: list[str] = []
    for route_prefix, skills_dir in skill_dirs:
        if not skills_dir.is_dir():
            continue
        routes[route_prefix] = FilesystemBackend(
            root_dir=skills_dir,
            virtual_mode=True,
        )
        permissions.append(
            FilesystemPermission(
                operations=["write"],
                paths=[f"{route_prefix}**"],
                mode="deny",
            )
        )
        skill_sources.append(route_prefix)

    if not routes:
        return runtime_backend, [], permissions

    backend = CompositeBackend(
        default=runtime_backend,
        routes=routes,
        artifacts_root="/.artifacts",
    )
    return backend, skill_sources, permissions
