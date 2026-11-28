"""在整条 system_message 最末尾注入按用户/按天变化的运行时提示片段。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from datetime import date

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

from melonclaw.core.prompts import build_dynamic_runtime_prompt


class RuntimePromptTailMiddleware(AgentMiddleware):
    """将当前用户的 MCP 服务清单与实时本地日期置于全部静态提示块之后。

    这样多用户共享或跨 0 点复用缓存 Agent 时，前段静态提示（基础指引、Skill、
    路由与 QuickJS 说明书）保持字节稳定，同时跨天调用也能拿到最新本地日期。
    """

    def __init__(
        self,
        mcp_server_names: Collection[str],
        *,
        date_provider: Callable[[], date] | None = None,
    ) -> None:
        super().__init__()
        self.mcp_server_names = tuple(sorted(mcp_server_names))
        self._date_provider = date_provider

    def _tail_prompt(self) -> str:
        today = self._date_provider() if self._date_provider is not None else None
        return build_dynamic_runtime_prompt(self.mcp_server_names, current_date=today)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(
            request.override(
                system_message=append_to_system_message(
                    request.system_message,
                    self._tail_prompt(),
                )
            )
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(
            request.override(
                system_message=append_to_system_message(
                    request.system_message,
                    self._tail_prompt(),
                )
            )
        )
