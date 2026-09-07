"""Deep Agents QuickJS Interpreter 的集中配置。"""

from __future__ import annotations

from langchain_quickjs import CodeInterpreterMiddleware


INTERPRETER_MEMORY_LIMIT = 64 * 1024 * 1024
INTERPRETER_TIMEOUT_SECONDS = 15.0
INTERPRETER_MAX_RESULT_CHARS = 4_000
INTERPRETER_MAX_PTC_CALLS = 8
INTERPRETER_PTC_TOOLS = ("internet_search",)


def build_interpreter_middleware() -> CodeInterpreterMiddleware:
    """创建能力收敛的线程级 JavaScript Interpreter。

    PTC 调用不会逐次经过父 Agent 的 ``interrupt_on``，因此这里只桥接
    无写入副作用的搜索工具。文件、Shell 和 MCP 工具仍走普通工具调用路径。
    """

    return CodeInterpreterMiddleware(
        memory_limit=INTERPRETER_MEMORY_LIMIT,
        timeout=INTERPRETER_TIMEOUT_SECONDS,
        max_ptc_calls=INTERPRETER_MAX_PTC_CALLS,
        max_result_chars=INTERPRETER_MAX_RESULT_CHARS,
        capture_console=True,
        subagents=True,
        ptc=list(INTERPRETER_PTC_TOOLS),
        mode="thread",
    )
