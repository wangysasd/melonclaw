"""无人值守工具的可见目录与调用双重校验；主/子 Agent 分别安装。"""

import json

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ExtendedModelResponse
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from melonclaw.core.task_policy import TaskExecutionError, TaskRunControl


class TaskContextLimitMiddleware(AgentMiddleware):
    """替换默认摘要插槽；无文件能力时不能隐式落盘、再用不存在的文件引用。"""

    def __init__(self, limit: int):
        self.limit = limit

    @property
    def name(self):
        return "SummarizationMiddleware"

    def _check(self, request):
        messages = list(request.messages)
        if request.system_message is not None:
            messages.insert(0, request.system_message)
        schemas = [{"name": t.name, "description": t.description, "args": t.args}
                   for t in request.tools if hasattr(t, "args")]
        messages.append(SystemMessage(content=json.dumps(schemas, ensure_ascii=False)))
        # 中英文混合内容采用保守近似值；供应商实际窗口错误仍由执行层收敛失败。
        if count_tokens_approximately(messages, chars_per_token=2.0) > self.limit:
            raise TaskExecutionError("context_limit", "任务上下文超过预算，请缩小任务范围或使用更大窗口模型。")

    def wrap_model_call(self, request, handler):
        self._check(request)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        self._check(request)
        return await handler(request)


class TaskPolicyMiddleware(AgentMiddleware):
    def __init__(self, allowed: frozenset[str], control: TaskRunControl):
        self.allowed = allowed
        self.control = control

    def _request(self, request):
        self.control.consume("model")
        return request.override(tools=[
            tool for tool in request.tools
            if getattr(tool, "name", None) in self.allowed
        ])

    def _response(self, response):
        self.control.check()
        value = response.model_response if isinstance(response, ExtendedModelResponse) else response
        messages = [value] if isinstance(value, AIMessage) else value.result
        # 整批预检，避免同一响应中合法搜索先执行、随后才发现危险工具。
        for message in messages:
            if isinstance(message, AIMessage):
                if message.invalid_tool_calls:
                    raise TaskExecutionError("tool_protocol_error", "模型工具参数无法解析。")
                for call in message.tool_calls:
                    self._check_tool(call.get("name"))
        return response

    def _check_tool(self, name):
        self.control.check()
        if name not in self.allowed:
            raise TaskExecutionError("tool_not_allowed", "任务请求了无人值守不支持的能力。")

    def wrap_model_call(self, request, handler):
        return self._response(handler(self._request(request)))

    async def awrap_model_call(self, request, handler):
        return self._response(await handler(self._request(request)))

    def wrap_tool_call(self, request, handler):
        self._check_tool(request.tool_call.get("name"))
        self.control.consume("tool")
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._check_tool(request.tool_call.get("name"))
        self.control.consume("tool")
        return await handler(request)
