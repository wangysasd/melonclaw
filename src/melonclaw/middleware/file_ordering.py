"""约束存在文件依赖的并发工具调用。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command


_MUTATING_FILE_TOOLS = frozenset({"write_file", "edit_file", "delete"})
ToolResult = ToolMessage | Command[Any]


class FileOperationOrderingMiddleware(AgentMiddleware):
    """阻止同一批次中“写后立即读”同一路径的竞态。"""

    @staticmethod
    def _mutated_paths(state: Any) -> set[str]:
        messages = state.get("messages", []) if isinstance(state, Mapping) else []
        last_ai_message = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)),
            None,
        )
        if last_ai_message is None or not last_ai_message.tool_calls:
            return set()

        mutated_paths: set[str] = set()
        for tool_call in last_ai_message.tool_calls:
            args = tool_call.get("args")
            path = args.get("file_path") if isinstance(args, Mapping) else None
            if tool_call.get("name") in _MUTATING_FILE_TOOLS and isinstance(path, str):
                mutated_paths.add(path)
        return mutated_paths

    @classmethod
    def _deferred_result(cls, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        if tool_call.get("name") != "read_file":
            return None
        args = tool_call.get("args")
        file_path = args.get("file_path") if isinstance(args, Mapping) else None
        if not isinstance(file_path, str) or file_path not in cls._mutated_paths(request.state):
            return None
        return ToolMessage(
            content=(
                "读取已延后：同一批调用中存在对该文件的写入、编辑或删除，"
                "无法保证读取时文件状态。请等待该文件操作的结果后，在下一轮"
                "单独调用 read_file。"
            ),
            name="read_file",
            tool_call_id=tool_call.get("id"),
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        """在工具执行层短路读取，并保留 AIMessage 中的原始调用。"""

        deferred = self._deferred_result(request)
        return deferred if deferred is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        """异步工具路径同样在执行层返回与调用 ID 对应的结果。"""

        deferred = self._deferred_result(request)
        return deferred if deferred is not None else await handler(request)
