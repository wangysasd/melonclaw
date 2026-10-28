"""提交当前缺失的工具能力请求；不调用候选工具或改变权限。"""

import json
from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import Field

from melonclaw.core.tool_catalog import FIND_TOOLS_NAME, selection_turn


def build_tool_discovery(max_requests: int = 4):
    """请求存入 Checkpoint，由 middleware 统一处理并行批次预算。"""

    @tool(FIND_TOOLS_NAME)
    def find_tools(
        runtime: ToolRuntime,
        query: Annotated[str, Field(min_length=1, max_length=512)] = "",
        tool_names: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]],
                              Field(max_length=64)] | None = None,
    ) -> Command:
        """补充工具：已知目录中的真实名称时用 tool_names 精确加载，否则用 query 描述能力。

        tool_names 仅允许当前授权目录中的完整名称，不接受猜测或前缀；提供时不调用选择模型。
        本次请求内下一次模型调用前返回最终加载结果；加载不代表已执行工具。
        """
        query = query.strip()
        if not query and not tool_names:
            raise ValueError("请提供工具需求或真实工具名称。")
        names = list(dict.fromkeys(tool_names or []))
        turn = selection_turn(runtime.state, runtime.context)
        requests = [r for r in runtime.state.get("tool_requests", []) if r["turn_id"] == turn]
        accepted = len(requests) < max_requests
        content = json.dumps({
            "status": "queued" if accepted else "exhausted",
            "hint": "需求已提交；本次请求内下一次模型调用前完成选择。检查可见工具并继续，不需要等待用户再次发消息；没有合适工具时如实说明。"
                    if accepted else "本消息检索额度已用完，请使用当前工具或说明能力不足。",
        }, ensure_ascii=False)
        return Command(update={
            "tool_requests": [{"turn_id": turn, "id": runtime.tool_call_id, "query": query,
                               "tool_names": names}] if accepted else [],
            "messages": [ToolMessage(id=f"tool-discovery:{runtime.tool_call_id}", content=content,
                                     tool_call_id=runtime.tool_call_id, name=FIND_TOOLS_NAME)],
        })

    return find_tools
