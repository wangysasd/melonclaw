"""在主模型响应提交到图状态前拒绝空工具名，阻断自动纠错循环。"""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage


class EmptyToolNameError(RuntimeError):
    """完整模型响应缺少工具名称；错误文本不携带参数或供应商内容。"""

    def __init__(self) -> None:
        super().__init__(
            "模型工具调用协议异常：工具名称为空，本轮已停止，"
            "该批工具未执行。请切换模型或联系管理员检查供应商接口。"
        )


class ToolNameGuardMiddleware(AgentMiddleware):
    """检查完整响应而非流式片段；任一空名称都使整批失败。"""

    @staticmethod
    def _validate(response: ModelResponse | AIMessage) -> None:
        messages = [response] if isinstance(response, AIMessage) else response.result
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            for call in [*message.tool_calls, *message.invalid_tool_calls]:
                name = call.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise EmptyToolNameError()

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse | AIMessage:
        response = handler(request)
        self._validate(response)
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
        response = await handler(request)
        self._validate(response)
        return response
