"""Deep Agents 最小命令行聊天示例。"""

from __future__ import annotations

import os
from pathlib import Path
from pprint import pprint
from typing import Any

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 修改这里切换输出方式：True 为 token 流式输出，False 为一次性输出。
STREAM_OUTPUT = True


@tool
def add_numbers(a: int, b: int) -> int:
    """计算两个整数的和。

    当用户要求计算两个整数的和时调用这个工具，不要直接心算。

    Args:
        a: 第一个整数。
        b: 第二个整数。
    """

    print(f"\n[tool 执行] add_numbers(a={a!r}, b={b!r})")
    return a + b


def build_model() -> ChatOpenAI:
    """使用仓库约定的 DeepSeek 环境变量创建 ChatModel。"""

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，请先在项目根目录的 .env 中配置。")

    return ChatOpenAI(
        model=(
            os.getenv("DEEPSEEK_MODEL_FLASH", "").strip()
            or os.getenv("DEEPSEEK_MODEL", "").strip()
            or "deepseek-chat"
        ),
        api_key=api_key,
        base_url=(
            os.getenv("DEEPSEEK_BASE_URL", "").strip()
            or "https://api.deepseek.com"
        ),
        temperature=0,
    )


def build_agent() -> Any:
    """配置一个带有 ``add_numbers`` 工具的最小 Deep Agent。"""

    return create_deep_agent(
        model=build_model(),
        tools=[add_numbers],
        system_prompt=(
            "你是一个简洁、友好的聊天助手。默认使用中文回答。"
            "当用户要求计算两个整数的和时，必须调用 add_numbers 工具。"
        ),
    )


def _content_to_text(content: Any) -> str:
    """从常见的消息 content 形态中提取可打印文本。"""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _print_state(result: dict[str, Any]) -> None:
    """打印一轮调用结束后的完整 state。"""

    print("\n=== 本轮 Agent 的完整 state ===")
    pprint(result, sort_dicts=False, width=120)

    print("\n=== Message 流：AIMessage -> ToolMessage ===")
    has_tool_call = False
    for index, message in enumerate(result.get("messages", [])):
        message_type = type(message).__name__
        print(f"[{index}] {message_type}: {message!r}")

        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            has_tool_call = True
            print(f"  AIMessage.tool_calls = {tool_calls!r}")

        if message_type == "ToolMessage":
            print(f"  ToolMessage.tool_call_id = {message.tool_call_id!r}")
            print(f"  ToolMessage.content = {message.content!r}")

    if not has_tool_call:
        print("本轮没有工具调用。试试：请用工具计算 12 + 30。")


def _run_stream(agent: Any, input_state: dict[str, Any]) -> dict[str, Any]:
    """流式打印 token，同时保留 values 流的最终 state。"""

    result: dict[str, Any] | None = None
    print("\n助手> ", end="", flush=True)
    for chunk in agent.stream(
        input_state,
        stream_mode=["messages", "values"],
        version="v2",
    ):
        if chunk["type"] == "messages":
            message_chunk, _metadata = chunk["data"]
            text = _content_to_text(message_chunk.content)
            if text:
                print(text, end="", flush=True)
        elif chunk["type"] == "values":
            result = chunk["data"]

    print()
    if result is None:
        raise RuntimeError("流式执行没有收到最终 values state。")
    _print_state(result)
    return result


def _run_turn(agent: Any, history: list[Any], user_input: str) -> dict[str, Any]:
    """执行一轮对话，并返回下一轮要继续使用的 state。"""

    input_state = {
        "messages": [
            *history,
            {"role": "user", "content": user_input},
        ]
    }
    if STREAM_OUTPUT:
        return _run_stream(agent, input_state)

    result = agent.invoke(input_state)
    _print_state(result)
    print("\n助手> ", end="")
    print(result["messages"][-1].content)
    return result


def main() -> None:
    """启动交互式命令行聊天。"""

    print("Deep Agents CLI Chat")
    print("输入 exit、quit 或 退出结束；也可以使用 Ctrl-D / Ctrl-C。")
    print(f"当前输出模式：{'流式' if STREAM_OUTPUT else '一次性'}")

    try:
        agent = build_agent()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    history: list[Any] = []
    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "退出"}:
            print("已退出。")
            break

        result = _run_turn(agent, history, user_input)
        history = result["messages"]


if __name__ == "__main__":
    main()
