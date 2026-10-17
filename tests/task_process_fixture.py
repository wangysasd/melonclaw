"""真实 Web 生命周期 + 内置调度器；仅由隔离 schema 测试启动。"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import create_async_engine
from test_task_agent import Model, resolved_model, settings_for

from melonclaw.api import lifespan
from melonclaw.api.app import create_app
from melonclaw.core import task_agent
from melonclaw.repository import BusinessRepository
from melonclaw.services import task_scheduler
from melonclaw.services.tasks import TaskService


async def run():
    schema = os.environ["TASK_TEST_SCHEMA"]
    assert schema.startswith("test_tasks_") and schema.replace("_", "").isalnum()
    engine = create_async_engine(
        os.environ["MELONCLAW_TEST_DATABASE_URL"],
        connect_args={"server_settings": {"search_path": schema}},
    )
    storage = BusinessRepository(SimpleNamespace(engine=engine))
    settings = settings_for(Path(os.environ["TASK_TEST_WORKDIR"]), task_poll_seconds=1)

    async def initialize():
        pass

    async def close():
        await engine.dispose()

    manager = SimpleNamespace(
        runtime=SimpleNamespace(tracing=None),
        storage=storage,
        settings=settings,
        ready=True,
        checkpointer=InMemorySaver(),
        initialize=initialize,
        close=close,
    )
    lifespan.ChatService = lambda: manager

    async def resolve(*args, **kwargs):
        return resolved_model()

    TaskService.resolve_model = resolve
    original = task_scheduler.build_research_agent

    async def controlled_build(*args, **kwargs):
        print("EXECUTING", flush=True)
        while not Path(os.environ["TASK_TEST_RELEASE"]).exists():
            await asyncio.sleep(0.05)
        return await original(*args, **kwargs)

    task_scheduler.build_research_agent = controlled_build
    task_agent.build_chat_model = lambda *a, **k: Model(
        responses=[AIMessage(content="Web 内置任务完成")]
    )
    app = create_app()
    await uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=int(sys.argv[1]), log_level="critical", access_log=False
        )
    ).serve()


if __name__ == "__main__":
    asyncio.run(run())
