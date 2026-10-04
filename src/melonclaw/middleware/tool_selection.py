"""按用户轮次选择应用工具；会话 Checkpoint 复用选择并按需扩展。"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Collection
from time import perf_counter
from typing import Any
from uuid import uuid4

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.constants import TAG_NOSTREAM
from langgraph.types import Command, Overwrite

from melonclaw.core.prompts import build_tool_selection_prompt
from melonclaw.core.tool_catalog import CatalogSelectionState, catalog_fingerprint, selection_turn
from melonclaw.output.content import answer_text

_JSON_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_selected_tool_names(
    value: str,
    valid_names: Collection[str],
    max_tools: int,
) -> list[str] | None:
    """解析选择模型返回的 JSON；无法解析时返回 ``None``。"""

    cleaned = _JSON_CODE_FENCE.sub("", value.strip())
    candidates = [cleaned]
    object_start, object_end = cleaned.find("{"), cleaned.rfind("}")
    array_start, array_end = cleaned.find("["), cleaned.rfind("]")
    if object_start >= 0 and object_end > object_start:
        candidates.append(cleaned[object_start : object_end + 1])
    if array_start >= 0 and array_end > array_start:
        candidates.append(cleaned[array_start : array_end + 1])

    decoded: Any = None
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    else:
        return None

    raw_names = decoded.get("tools") if isinstance(decoded, dict) else decoded
    if not isinstance(raw_names, list):
        return None

    valid = set(valid_names)
    selected: list[str] = []
    for name in raw_names:
        if isinstance(name, str) and name in valid and name not in selected:
            selected.append(name)
            if len(selected) == max_tools:
                break
    return selected if selected or not raw_names else None


class CatalogToolSelectorMiddleware(AgentMiddleware):
    """注册全部工具，但每轮只把相关子集交给主模型。"""

    state_schema = CatalogSelectionState

    def __init__(
        self,
        *,
        model: BaseChatModel,
        catalog_tool_names: Collection[str],
        max_tools: int,
        timeout_seconds: int = 10,
    ) -> None:
        super().__init__()
        if max_tools < 1:
            raise ValueError("max_tools 必须大于 0。")
        self.model = model
        self.catalog_tool_names = frozenset(catalog_tool_names)
        self.max_tools = max_tools
        self.timeout_seconds = timeout_seconds

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage | ExtendedModelResponse:
        """选择应用工具子集，并保留 Deep Agents 自带工具。"""

        catalog_tools = [
            tool
            for tool in request.tools
            if not isinstance(tool, dict) and tool.name in self.catalog_tool_names
        ]
        if len(catalog_tools) <= self.max_tools:
            return await handler(request)

        last_user_message = next(
            (
                message
                for message in reversed(request.messages)
                if isinstance(message, HumanMessage)
            ),
            None,
        )
        if last_user_message is None:
            return await handler(request)

        turn_id = selection_turn(request.state, request.runtime.context)
        fingerprint = catalog_fingerprint(catalog_tools)
        cached = request.state.get("tool_selection")
        valid_names = [tool.name for tool in catalog_tools]
        update = None
        if cached and cached["turn_id"] == turn_id and cached["fingerprint"] == fingerprint:
            selected_names = cached["names"]
        else:
            selection_prompt = build_tool_selection_prompt(
                [(tool.name, tool.description or "（无描述）") for tool in catalog_tools], self.max_tools,
            )
            activity_id = str(uuid4())
            started = perf_counter()
            writer = request.runtime.stream_writer
            writer({"type": "run_phase", "phase": "selecting_tools"})
            writer({"type": "run_activity", "id": activity_id, "kind": "selection", "status": "started"})
            outcome = "selected"
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    response = await self.model.ainvoke(
                        [SystemMessage(content=selection_prompt), last_user_message],
                        config={"tags": ["tool-selector", TAG_NOSTREAM], "metadata": {"tool_selector": True}},
                    )
                selected_names = _parse_selected_tool_names(answer_text(response.content), valid_names, self.max_tools)
                if selected_names is None:
                    raise ValueError("工具选择响应无效")
            except Exception:  # noqa: BLE001 - 内部只读路由失败可降级；取消不被捕获
                selected_names = []
                outcome = "degraded"
            writer({"type": "run_activity", "id": activity_id, "kind": "selection",
                    "status": "completed", "duration_ms": int((perf_counter() - started) * 1000),
                    "outcome": outcome})
            writer({"type": "run_phase", "phase": "waiting_model"})
            update = {"tool_selection": {"turn_id": turn_id, "fingerprint": fingerprint,
                                         "names": selected_names, "outcome": outcome},
                      "tool_discoveries": Overwrite([])}

        selected = set(selected_names)
        if update is None:
            for discovery in request.state.get("tool_discoveries", []):
                if discovery["turn_id"] == turn_id:
                    selected.update(set(discovery["names"]) & set(valid_names))
        filtered_tools = [
            tool
            for tool in request.tools
            if isinstance(tool, dict)
            or tool.name not in self.catalog_tool_names
            or tool.name in selected
        ]
        response = await handler(request.override(tools=filtered_tools))
        if update is None:
            return response
        return ExtendedModelResponse(model_response=response, command=Command(update=update))
