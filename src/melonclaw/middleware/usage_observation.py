"""观测最终发往模型的上下文估算；精确用量由执行 callback 采集。"""

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages.utils import count_tokens_approximately


class UsageObservationMiddleware(AgentMiddleware):
    def __init__(self, *, context_window: int, trigger_tokens: int, scope: str = "main"):
        self.context_window = context_window
        self.trigger_tokens = trigger_tokens
        self.scope = scope

    async def awrap_model_call(self, request, handler):
        messages = [*([request.system_message] if request.system_message else []), *request.messages]
        estimated = int(count_tokens_approximately(messages, tools=request.tools))
        request.runtime.stream_writer({"type": "context_usage", "scope": self.scope,
                                       "estimated_input_tokens": estimated,
                                       "context_window": self.context_window,
                                       "summary_trigger_tokens": self.trigger_tokens})
        return await handler(request)
