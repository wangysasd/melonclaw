"""在主 Agent 中注入受控的 Global/Tenant/User Memory。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)

from melonclaw.core.memory import MemoryService
from melonclaw.tool.memory import build_memory_tools


class MemoryScopeMiddleware(AgentMiddleware):
    """每次模型调用按 Runtime 上下文动态加载长期 Memory。

    不把记忆内容写入 Agent state，避免同一 thread 的旧内容在身份变化后
    被复用。Conversation 不绑定 tenant；这里按当前 Runtime 重新计算
    namespace，并把数据作为低信任参考资料注入。
    """

    def __init__(self, service: MemoryService) -> None:
        super().__init__()
        self.service = service
        self.tools = build_memory_tools(service)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        prompt = await self.service.load_prompt(request.runtime.context)
        if prompt:
            request = request.override(
                system_message=append_to_system_message(
                    request.system_message,
                    prompt,
                )
            )
        return await handler(request)


__all__ = ["MemoryScopeMiddleware"]
