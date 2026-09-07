"""长期 Memory 的固定参数 Agent 工具。"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from melonclaw.core.memory import MemoryScope, MemoryService


def _result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _scope(value: str) -> MemoryScope:
    if value not in {"global", "tenant", "user"}:
        raise ValueError("scope 只能是 global、tenant 或 user。")
    return value  # type: ignore[return-value]


def build_memory_tools(service: MemoryService) -> list[BaseTool]:
    """构造绑定当前应用 MemoryService 的工具，不暴露身份参数。"""

    @tool
    async def search_memory(
        query: str,
        scope: str = "all",
        *,
        runtime: ToolRuntime,
    ) -> str:
        """搜索当前运行有权访问的 Global、Tenant 或 User Memory。"""

        return _result(await service.search(runtime.context, query, scope))

    @tool
    async def read_memory(
        scope: str,
        key: str,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """读取当前运行有权访问的指定 Memory key。"""

        value = await service.read(runtime.context, _scope(scope), key)
        return _result(value or {"status": "not_found", "scope": scope, "key": key})

    @tool
    async def remember_user_memory(
        content: str,
        key: str = "profile",
        replaces: str | None = None,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """仅在用户明确要求记住时，保存当前用户的长期偏好或背景。"""

        value = await service.remember_user(
            runtime.context,
            content,
            key=key,
            replaces=replaces,
        )
        return _result(value)

    @tool
    async def forget_user_memory(
        key: str = "profile",
        content: str | None = None,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """删除当前用户 Memory key，或删除其中一条精确内容。"""

        value = await service.forget_user(
            runtime.context,
            key=key,
            content=content,
        )
        return _result(value)

    @tool
    async def propose_tenant_memory(
        content: str,
        key: str = "shared",
        replaces: str | None = None,
        *,
        runtime: ToolRuntime,
    ) -> str:
        """提出租户共享记忆变更；不会直接修改租户已发布内容。"""

        value = await service.propose_tenant(
            runtime.context,
            content,
            key=key,
            replaces=replaces,
        )
        return _result(value)

    return [
        search_memory,
        read_memory,
        remember_user_memory,
        forget_user_memory,
        propose_tenant_memory,
    ]


__all__ = ["build_memory_tools"]
