"""从完整应用工具目录中为每轮主模型请求动态选择相关工具。"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Collection
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from melonclaw.output.content import content_to_text
from melonclaw.core.prompts import build_tool_selection_prompt


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
    return selected


def _mentioned_tool_names(
    value: str,
    valid_names: Collection[str],
    max_tools: int,
) -> list[str]:
    """JSON 解析失败时，从普通文本中提取明确出现的合法工具名。"""

    mentioned: list[str] = []
    for name in valid_names:
        pattern = rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])"
        if re.search(pattern, value) is not None:
            mentioned.append(name)
            if len(mentioned) == max_tools:
                break
    return mentioned


class CatalogToolSelectorMiddleware(AgentMiddleware):
    """注册全部工具，但每轮只把相关子集交给主模型。"""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        catalog_tool_names: Collection[str],
        max_tools: int,
    ) -> None:
        super().__init__()
        if max_tools < 1:
            raise ValueError("max_tools 必须大于 0。")
        self.model = model
        self.catalog_tool_names = frozenset(catalog_tool_names)
        self.max_tools = max_tools

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | AIMessage:
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

        selection_prompt = build_tool_selection_prompt(
            [(tool.name, tool.description or "（无描述）") for tool in catalog_tools],
            self.max_tools,
        )
        response = await self.model.ainvoke(
            [SystemMessage(content=selection_prompt), last_user_message],
            config={
                "tags": ["tool-selector"],
                "metadata": {"tool_selector": True},
            },
        )
        response_text = content_to_text(response.content)
        valid_names = [tool.name for tool in catalog_tools]
        selected_names = _parse_selected_tool_names(
            response_text,
            valid_names,
            self.max_tools,
        )
        if selected_names is None:
            selected_names = _mentioned_tool_names(
                response_text,
                valid_names,
                self.max_tools,
            )
            if not selected_names:
                selected_names = valid_names[: self.max_tools]

        selected = set(selected_names)
        filtered_tools = [
            tool
            for tool in request.tools
            if isinstance(tool, dict)
            or tool.name not in self.catalog_tool_names
            or tool.name in selected
        ]
        if selected_names:
            print(f"\n🧭 [动态工具选择] {', '.join(selected_names)}", flush=True)
        return await handler(request.override(tools=filtered_tools))
