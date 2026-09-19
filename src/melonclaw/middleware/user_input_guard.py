"""阻止 ask_user 与其他工具在同一批次中并发执行。

``interrupt()`` 只暂停发起它的那一个工具调用，同一批里的其它工具照常执行。
如果模型把 ``ask_user`` 和 ``write_file`` / ``execute`` 放在同一批，副作用会在
用户回答之前发生；恢复执行时中断点所在的节点还可能被重跑一次，等于把同一个
副作用执行两遍。

设计文档 §4.2 的约定是：含 ``ask_user`` 且调用数不为 1 的批次**整批不执行**，
逐个生成与原 ``tool_call_id`` 对应的错误结果，让模型下一轮单独提问。只拦
``ask_user`` 自己是不够的——那样同一批的副作用工具照样会执行完。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from melonclaw.core.user_input import USER_INPUT_TOOL_NAME

# 一次只能挂起一个问题批次：多道题应放进一次调用的 questions 数组，
# 不能在同批里连着调用两次 ask_user。
ASK_USER_REJECT_MESSAGE = (
    "ask_user 不能和其他工具在同一批调用中一起执行。"
    "请单独调用 ask_user 提问，等待用户回答后再继续其他操作。"
)
BATCH_REJECT_MESSAGE = (
    "本批调用已整体取消：同一批里包含 ask_user，其他工具不能在用户回答之前执行。"
    "请等提问结束后，在下一轮单独调用本工具。"
)

ToolResult = ToolMessage | Command[Any]


class UserInputGuardMiddleware(AgentMiddleware):
    """含 ask_user 且调用数不为 1 的批次，整批短路为错误结果。"""

    @staticmethod
    def _batch_tool_call_names(state: Any) -> list[str]:
        """返回当前这批工具调用的工具名。"""

        messages = state.get("messages", []) if isinstance(state, Mapping) else []
        last = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)),
            None,
        )
        if last is None or not last.tool_calls:
            return []
        return [str(call.get("name", "")) for call in last.tool_calls]

    def _rejection(self, request: ToolCallRequest) -> ToolMessage | None:
        """命中护栏时返回错误结果；否则返回 None 表示放行。"""

        tool_call = request.tool_call
        name = str(tool_call.get("name", ""))
        names = self._batch_tool_call_names(request.state)
        if USER_INPUT_TOOL_NAME not in names or len(names) == 1:
            return None
        return ToolMessage(
            content=(
                ASK_USER_REJECT_MESSAGE
                if name == USER_INPUT_TOOL_NAME
                else BATCH_REJECT_MESSAGE
            ),
            name=name,
            tool_call_id=tool_call.get("id"),
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        """同步工具路径也在执行层短路，保留与调用 ID 对应的错误结果。"""

        rejection = self._rejection(request)
        return rejection if rejection is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        """异步工具路径同样在执行层短路，不调用 handler 即不执行工具。"""

        rejection = self._rejection(request)
        return rejection if rejection is not None else await handler(request)
