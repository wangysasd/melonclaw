"""命令行入口：运行 Deep Agents quickstart 研究 Agent。"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from melonclaw.core.agent import build_research_agent
from melonclaw.core.config import load_settings
from melonclaw.core.database import open_checkpoint_pool
from melonclaw.core.hitl import aget_pending_approval, request_human_decision
from melonclaw.output.streaming import stream_research


EXIT_COMMANDS = {"exit", "quit", "q", ":q"}
HUMAN_PROMPT = "🧑> "
AI_LABEL = "🤖> "


async def run_chat_loop(agent: Any) -> None:
    """在同一个事件循环中执行多轮模型流和异步 MCP 工具。"""

    config = {"configurable": {"thread_id": "deepagents-quickstart"}}

    print("多轮对话模式：输入 exit / quit / q 退出。", flush=True)

    while True:
        try:
            line = input(f"\n{HUMAN_PROMPT}").strip()
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            break

        if not line:
            continue
        if line.lower() in EXIT_COMMANDS:
            break

        print(f"\n{AI_LABEL}", end="", flush=True)
        await _run_with_human_approval(
            agent,
            {"messages": [{"role": "user", "content": line}]},
            config,
        )


async def _run_with_human_approval(
    agent: Any,
    agent_input: Any,
    config: dict[str, Any],
) -> None:
    """运行到结束或中断；每个中断都由用户显式决定后再恢复。"""

    await stream_research(agent, agent_input, config)
    while pending := await aget_pending_approval(agent, config):
        try:
            resume = request_human_decision(pending)
        except (EOFError, KeyboardInterrupt):
            print("\n审批未提交；该 Agent 运行仍停在当前 checkpoint。")
            return
        print(f"\n{AI_LABEL}", end="", flush=True)
        await stream_research(agent, resume, config)


async def run_application() -> None:
    """在单个事件循环中打开 PostgreSQL Checkpointer 并运行 CLI。"""

    settings = load_settings()
    checkpoint_pool = await open_checkpoint_pool(settings.database_url)
    try:
        checkpointer = AsyncPostgresSaver(checkpoint_pool)
        agent = await build_research_agent(
            settings,
            checkpointer=checkpointer,
        )
        await run_chat_loop(agent)
    finally:
        await checkpoint_pool.close()


def main() -> None:
    asyncio.run(run_application())


if __name__ == "__main__":
    main()
