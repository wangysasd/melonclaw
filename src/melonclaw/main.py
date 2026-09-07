"""命令行入口：运行 MelonClaw 通用 AI 助手。"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from melonclaw.core.agent import AgentContext, build_research_agent
from melonclaw.core.config import load_settings
from melonclaw.core.database import (
    BusinessDatabase,
    close_memory_store,
    open_checkpoint_pool,
    open_memory_store,
)
from melonclaw.core.hitl import aget_pending_approval, request_human_decision
from melonclaw.core.memory import MemoryService
from melonclaw.output.streaming import stream_research

EXIT_COMMANDS = {"exit", "quit", "q", ":q"}
HUMAN_PROMPT = "🧑> "
AI_LABEL = "🤖> "


async def run_chat_loop(agent: Any, *, worker_id: str = "cli-local") -> None:
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

        context = AgentContext(
            user_id="zhangsan",
            tenant_id="research",
            tenant_name="研究",
            request_id=str(uuid4()),
            run_id=str(uuid4()),
            worker_id=worker_id,
            memory_enabled=True,
        )
        print(f"\n{AI_LABEL}", end="", flush=True)
        await _run_with_human_approval(
            agent,
            {"messages": [{"role": "user", "content": line}]},
            config,
            context=context,
        )


async def _run_with_human_approval(
    agent: Any,
    agent_input: Any,
    config: dict[str, Any],
    *,
    context: AgentContext | None = None,
) -> None:
    """运行到结束或中断；每个中断都由用户显式决定后再恢复。"""

    await stream_research(agent, agent_input, config, context=context)
    while pending := await aget_pending_approval(agent, config):
        try:
            resume = request_human_decision(pending)
        except (EOFError, KeyboardInterrupt):
            print("\n审批未提交；该 Agent 运行仍停在当前 checkpoint。")
            return
        print(f"\n{AI_LABEL}", end="", flush=True)
        await stream_research(agent, resume, config, context=context)


async def run_application() -> None:
    """在单个事件循环中打开 PostgreSQL 持久化并运行 CLI。"""

    settings = load_settings()
    database = BusinessDatabase(settings.database_url)
    checkpoint_pool = None
    memory_store_context = None
    try:
        await database.open()
        await database.verify_schema(require_checkpointer=True, require_store=True)
        memory_store_context, memory_store = await open_memory_store(
            settings.database_url
        )
        checkpoint_pool = await open_checkpoint_pool(settings.database_url)
        checkpointer = AsyncPostgresSaver(checkpoint_pool)
        memory_service = MemoryService(database, memory_store)
        agent = await build_research_agent(
            settings,
            checkpointer=checkpointer,
            memory_service=memory_service,
        )
        await run_chat_loop(agent, worker_id=f"cli-{uuid4()}")
    finally:
        if memory_store_context is not None:
            await close_memory_store(memory_store_context)
        if checkpoint_pool is not None:
            await checkpoint_pool.close()
        await database.close()


def main() -> None:
    asyncio.run(run_application())


if __name__ == "__main__":
    main()
