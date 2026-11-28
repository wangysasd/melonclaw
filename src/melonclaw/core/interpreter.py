"""Deep Agents QuickJS Interpreter 的集中配置。"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import ModelRequest
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langchain_quickjs._ptc import to_camel_case
from langchain_quickjs.middleware import (
    _resolve_thread_id,
    filter_tools_for_ptc,
    find_subagent_task_tool,
)

INTERPRETER_MEMORY_LIMIT = 64 * 1024 * 1024
INTERPRETER_TIMEOUT_SECONDS = 15.0
INTERPRETER_MAX_RESULT_CHARS = 4_000
INTERPRETER_MAX_PTC_CALLS = 8
INTERPRETER_PTC_TOOLS = ("internet_search", "web_fetch")

_COMPACT_SUBAGENT_PROMPT = (
    "\n\n### Dispatching Subagents from `eval`\n"
    "Inside `eval`, call `await task({ description, subagentType, label?, responseSchema? })` "
    "to run a configured subagent asynchronously (use `Promise.all` for concurrent fan-out and "
    'reuse JS variables instead of re-typing large payloads). Available `subagentType`: `"general-purpose"`.'
)


def _schema_type_label(schema: dict[str, Any]) -> str:
    if isinstance(schema.get("type"), str):
        return schema["type"]
    variants = [
        item["type"]
        for item in (schema.get("anyOf") or [])
        if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"] != "null"
    ]
    return " | ".join(dict.fromkeys(variants)) or "unknown"


def _render_compact_ptc_prompt(exposed: list[BaseTool]) -> str:
    """生成紧凑的 tools.* 签名说明，避免重复各工具 Schema 中的长文档。"""

    if not exposed:
        return ""
    lines = [
        "\n\n### `tools` namespace in `eval`",
        "Call `await tools.<name>({ ... })` (combine independent calls with `Promise.all`; "
        "results are native JS values, no `JSON.parse` needed; see tool schemas for full parameter docs):",
        "```typescript",
    ]
    for item in exposed:
        schema = item.get_input_jsonschema() if hasattr(item, "get_input_jsonschema") else {}
        props = schema.get("properties") if isinstance(schema, dict) else {}
        required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
        params = [
            f"{param}{'' if param in required else '?'}: {_schema_type_label(spec if isinstance(spec, dict) else {})}"
            for param, spec in (props.items() if isinstance(props, dict) else ())
        ]
        lines.append(f"tools.{to_camel_case(item.name)}({{ {', '.join(params)} }}): Promise<unknown>;")
    lines.append("```")
    return "\n".join(lines)


class LazyCodeInterpreterMiddleware(CodeInterpreterMiddleware):
    """推迟 QuickJS 实例化到真正调用 eval 时，并使用紧凑的 PTC/Subagent 提示。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._ptc_installed_by_thread: dict[str, tuple[BaseTool, ...]] = {}

    def _prepare_for_call(self, request: ModelRequest[Any]) -> str:
        request_tools: list[BaseTool] = list(getattr(request, "tools", []) or [])

        subagent_section = ""
        if self._subagents and find_subagent_task_tool(request_tools) is not None:
            subagent_section = _COMPACT_SUBAGENT_PROMPT

        if self._ptc is None:
            return self._base_prompt(ptc_attached=False) + subagent_section

        exposed = filter_tools_for_ptc(
            request_tools,
            self._ptc,
            self_tool_name=self._tool_name,
        )
        prompt = self._base_prompt(ptc_attached=bool(exposed)) + subagent_section
        thread_id = _resolve_thread_id(self._fallback_thread_id)
        exposed_tuple = tuple(exposed)
        self._ptc_tools_by_thread[thread_id] = exposed_tuple
        # 仅在该线程已存在 REPL（如已调用过 eval 或恢复了历史快照）时同步工具，
        # 不在模型调用前提前启动 QuickJS 虚拟机。
        repl = self._registry.get_if_exists(thread_id)
        if repl is not None and self._ptc_installed_by_thread.get(thread_id) != exposed_tuple:
            repl.install_tools(exposed)
            self._ptc_installed_by_thread[thread_id] = exposed_tuple
        exposed_names = frozenset(t.name for t in exposed)
        if self._ptc_prompt_cache is None or self._ptc_prompt_cache[0] != exposed_names:
            self._ptc_prompt_cache = (
                exposed_names,
                _render_compact_ptc_prompt(exposed),
            )
        return prompt + self._ptc_prompt_cache[1]

    def _repl_for_eval(self, thread_id: str) -> Any:
        repl = self._registry.get(thread_id)
        if self._ptc is not None:
            tools = self._ptc_tools_by_thread.get(thread_id, ())
            if self._mode == "call" or self._ptc_installed_by_thread.get(thread_id) != tools:
                repl.install_tools(list(tools))
                self._ptc_installed_by_thread[thread_id] = tools
        return repl

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        thread_id = _resolve_thread_id(self._fallback_thread_id)
        self._ptc_installed_by_thread.pop(thread_id, None)
        return super().after_agent(state, runtime)

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        thread_id = _resolve_thread_id(self._fallback_thread_id)
        self._ptc_installed_by_thread.pop(thread_id, None)
        return await super().aafter_agent(state, runtime)


def build_interpreter_middleware() -> CodeInterpreterMiddleware:
    """创建能力收敛的线程级 JavaScript Interpreter。

    PTC 调用不会逐次经过父 Agent 的 ``interrupt_on``，因此这里只桥接
    无写入副作用的公开网页搜索与读取工具。文件、Shell 和 MCP 工具仍走普通工具调用路径。
    """

    return LazyCodeInterpreterMiddleware(
        memory_limit=INTERPRETER_MEMORY_LIMIT,
        timeout=INTERPRETER_TIMEOUT_SECONDS,
        max_ptc_calls=INTERPRETER_MAX_PTC_CALLS,
        max_result_chars=INTERPRETER_MAX_RESULT_CHARS,
        capture_console=True,
        subagents=True,
        ptc=list(INTERPRETER_PTC_TOOLS),
        mode="thread",
    )
