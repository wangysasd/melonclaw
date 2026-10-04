"""让官方摘要在计数前使用实际可见的会话工具池。"""

from deepagents.middleware.summarization import SummarizationMiddleware

from melonclaw.middleware.tool_selection import ToolPoolMiddleware


class ToolPoolSummarizationMiddleware(SummarizationMiddleware):
    def __init__(self, model, *, tool_pool: ToolPoolMiddleware | None = None, **kwargs):
        super().__init__(model=model, **kwargs)
        self.tool_pool = tool_pool

    @property
    def name(self) -> str:
        # Deep Agents 按名称原位替换；沿用默认插槽，避免叠加第二个摘要实例。
        return "SummarizationMiddleware"

    def wrap_model_call(self, request, handler):
        if self.tool_pool is not None:
            request = self.tool_pool.filter_request(request)
        return super().wrap_model_call(request, handler)

    async def awrap_model_call(self, request, handler):
        if self.tool_pool is not None:
            request = self.tool_pool.filter_request(request)
        return await super().awrap_model_call(request, handler)
