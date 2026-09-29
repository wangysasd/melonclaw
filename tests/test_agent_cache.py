"""Agent 缓存淘汰不遗留构建锁，同一键并发只构建一次。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from melonclaw.services import runtime as runtime_module
from melonclaw.services.runtime import ChatRuntime


def test_agent_build_locks_cover_waiters_and_are_released(monkeypatch, tmp_path):
    builds: list[str] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def build_agent(settings, *, workspace_dir, **kwargs):
        builds.append(str(workspace_dir))
        started.set()
        await release.wait()
        return SimpleNamespace()

    class FakeStorage:
        async def list_visible_skill_rows(self, user_id, **kwargs):
            return []

        async def skills_revision(self):
            return "skills-rev"

        async def mcp_revision(self):
            return "mcp-rev"

        async def models_revision(self):
            return "models-rev"

        async def list_visible_mcp_rows(self, user_id):
            return []

    monkeypatch.setattr(runtime_module, "build_research_agent", build_agent)
    runtime = ChatRuntime(
        settings=SimpleNamespace(
            agent_cache_entries=1,
            data_root=tmp_path,
        ),
        storage=FakeStorage(),
        checkpointer=object(),
        memory_store=object(),
        memory_service=object(),
        workspace_agents={},
    )
    runtime.workspace_dir = lambda conversation, project: Path("agent-cache-test") / conversation["id"]
    model = SimpleNamespace(cache_key=("model",))
    first = {"id": str(uuid4()), "user_id": "user"}
    second = {"id": str(uuid4()), "user_id": "user"}

    async def run():
        first_task = asyncio.create_task(runtime.agent_for_conversation(first, None, model))
        await started.wait()
        waiter = asyncio.create_task(runtime.agent_for_conversation(first, None, model))
        await asyncio.sleep(0)
        assert len(runtime.agent_build_locks) == 1
        release.set()
        first_agent, waiting_agent = await asyncio.gather(first_task, waiter)
        assert first_agent is waiting_agent
        await runtime.agent_for_conversation(second, None, model)

    asyncio.run(run())

    assert builds == [
        str(Path("agent-cache-test") / first["id"]),
        str(Path("agent-cache-test") / second["id"]),
    ]
    assert runtime.agent_build_locks == {}
    assert len(runtime.workspace_agents) == 1
