"""约束存在文件依赖的并发工具调用。"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime


_MUTATING_FILE_TOOLS = frozenset({"write_file", "edit_file", "delete"})


class FileOperationOrderingMiddleware(AgentMiddleware):
    """阻止同一批次中“写后立即读”同一路径的竞态。"""

    def after_model(
        self,
        state: AgentState,
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        last_ai_message = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)),
            None,
        )
        if last_ai_message is None or not last_ai_message.tool_calls:
            return None

        mutated_paths = {
            path
            for tool_call in last_ai_message.tool_calls
            if tool_call["name"] in _MUTATING_FILE_TOOLS
            if isinstance(path := tool_call.get("args", {}).get("file_path"), str)
        }
        if not mutated_paths:
            return None

        deferred_calls = [
            tool_call
            for tool_call in last_ai_message.tool_calls
            if tool_call["name"] == "read_file"
            and tool_call.get("args", {}).get("file_path") in mutated_paths
        ]
        if not deferred_calls:
            return None

        deferred_ids = {tool_call["id"] for tool_call in deferred_calls}
        last_ai_message.tool_calls = [
            tool_call
            for tool_call in last_ai_message.tool_calls
            if tool_call["id"] not in deferred_ids
        ]
        messages_to_model = [
            ToolMessage(
                content=(
                    "读取已延后：同一批调用中存在对该文件的写入、编辑或删除，"
                    "无法保证读取时文件状态。请等待该文件操作的结果后，在下一轮"
                    "单独调用 read_file。"
                ),
                name="read_file",
                tool_call_id=tool_call["id"],
                status="error",
            )
            for tool_call in deferred_calls
        ]
        return {"messages": [last_ai_message, *messages_to_model]}

    async def aafter_model(
        self,
        state: AgentState,
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """异步 Agent 路径复用相同的纯状态转换。"""

        return self.after_model(state, runtime)
