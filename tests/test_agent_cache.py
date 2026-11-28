"""Agent 缓存淘汰不遗留构建锁，同一键并发只构建一次。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from melonclaw.repository.resource_versions import ResourceVersions
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
        return SimpleNamespace(melonclaw_mcp_failed=False)

    class FakeStorage:
        def __init__(self):
            self.resource_versions = ResourceVersions()

        async def effective_environment(self, user_id):
            return {}

        async def list_visible_skill_rows(self, user_id, **kwargs):
            return []

        async def skills_revision(self):
            return "skills-rev"

        async def mcp_revision(self):
            return "mcp-rev"

        async def get_user_context(self, user_id):
            return SimpleNamespace(tenant_id="system", user_id=user_id)

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
    model = "custom:model"
    async def resolve(*args):
        return SimpleNamespace(profile_id=model)
    runtime.resolve_model = resolve
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


def test_warm_cache_reads_no_resources_and_invalidates_only_affected_scope(monkeypatch, tmp_path):
    from collections import Counter
    from unittest.mock import AsyncMock

    counts = Counter()
    class Storage:
        resource_versions = ResourceVersions()
        async def list_visible_mcp_rows(self, user_id):
            counts["mcp"] += 1
            return []
        async def effective_environment(self, user_id):
            counts["environment"] += 1
            return {"DEMO": "fixture"}
    storage = Storage()
    async def snapshot(*args):
        counts["skill_snapshot"] += 1
        return "revision", (), []
    async def build(*args, **kwargs):
        counts["build"] += 1
        return SimpleNamespace(melonclaw_mcp_failed=False)
    monkeypatch.setattr(runtime_module, "skill_snapshot", snapshot)
    monkeypatch.setattr(runtime_module, "build_research_agent", build)
    runtime = ChatRuntime(settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=storage, checkpointer=object(), memory_store=object(), memory_service=object())
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="custom:model"))
    conversation = {"id": str(uuid4()), "user_id": "alice"}
    context = SimpleNamespace(user_id="alice", tenant_id="tenant")
    async def run():
        first = await runtime.agent_for_conversation(conversation, None, user_context=context)
        baseline = counts.copy()
        for _ in range(100):
            assert await runtime.agent_for_conversation(conversation, None, user_context=context) is first
        assert counts == baseline and runtime.resolve_model.await_count == 1
        storage.resource_versions.publish("bob")
        assert await runtime.agent_for_conversation(conversation, None, user_context=context) is first
        storage.resource_versions.publish("alice")
        second = await runtime.agent_for_conversation(conversation, None, user_context=context)
        assert second is not first
        storage.resource_versions.publish(None)
        assert await runtime.agent_for_conversation(conversation, None, user_context=context) is not second
        assert counts == {"mcp": 3, "environment": 3, "skill_snapshot": 3, "build": 3}
    asyncio.run(run())


def test_build_during_resource_change_retries_and_does_not_publish_stale_graph(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    versions = ResourceVersions()
    started, release = asyncio.Event(), asyncio.Event()
    builds = []
    storage = SimpleNamespace(resource_versions=versions,
        list_visible_mcp_rows=AsyncMock(return_value=[]), effective_environment=AsyncMock(return_value={}))
    async def build(*args, **kwargs):
        agent = SimpleNamespace(melonclaw_mcp_failed=False)
        builds.append(agent)
        if len(builds) == 1:
            started.set()
            await release.wait()
        return agent
    monkeypatch.setattr(runtime_module, "build_research_agent", build)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=storage, checkpointer=object(), memory_store=object(), memory_service=object())
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="custom:model"))
    async def run():
        job = asyncio.create_task(runtime.agent_for_conversation({"id": str(uuid4()), "user_id": "alice"}, None,
            user_context=SimpleNamespace(user_id="alice", tenant_id="tenant")))
        await started.wait()
        versions.publish("alice")
        release.set()
        result = await job
        assert len(builds) == 2 and result is builds[1]
        assert all(agent is not builds[0] for agent in runtime.workspace_agents.values())
        assert runtime.agent_build_locks == {}
    asyncio.run(run())


def test_each_cache_factor_isolated(monkeypatch, tmp_path):
    """Each of the six inputs that changes an Agent gets a separate instance."""
    from unittest.mock import AsyncMock

    storage = SimpleNamespace(
        resource_versions=ResourceVersions(),
        list_visible_mcp_rows=AsyncMock(return_value=[]),
        effective_environment=AsyncMock(return_value={}),
    )
    built = []

    async def build(*args, **kwargs):
        agent = SimpleNamespace(melonclaw_mcp_failed=False)
        built.append(agent)
        return agent

    monkeypatch.setattr(runtime_module, "build_research_agent", build)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=storage, checkpointer=object(), memory_store=object(), memory_service=object(),
    )

    async def resolve(user_id, model_id=None):
        return SimpleNamespace(profile_id=model_id or "default", requested=model_id)

    runtime.resolve_model = AsyncMock(side_effect=resolve)
    conversation = {"id": str(uuid4()), "user_id": "alice"}
    context = SimpleNamespace(user_id="alice", tenant_id="tenant-a")

    async def run():
        baseline = await runtime.agent_for_conversation(conversation, None, user_context=context)
        assert await runtime.agent_for_conversation(conversation, None, user_context=context) is baseline
        selected_model = "model:fallback"
        explicitly_selected = await runtime.agent_for_conversation(
            conversation, None, selected_model, user_context=context,
        )
        restored_with_default_fallback = await runtime.agent_for_conversation(
            conversation, None, selected_model, user_context=context, fallback_default=True,
        )
        assert explicitly_selected is not restored_with_default_fallback

        variations = [
            # Workspace identity
            (dict(conversation, id=str(uuid4())), None, None, context),
            # Tenant identity
            (conversation, None, None, SimpleNamespace(user_id="alice", tenant_id="tenant-b")),
            # Explicit model selection
            (conversation, None, "model:other", context),
            # Client capabilities
            (conversation, None, None, context),
            # Global resource generation
            (conversation, None, None, context),
            # Current user's resource generation
            (conversation, None, None, context),
        ]
        agents = []
        for index, (conv, project, model, user_context) in enumerate(variations):
            if index == 3:
                agents.append(await runtime.agent_for_conversation(conv, project, model, ["user_input_v1"], user_context=user_context))
            else:
                agents.append(await runtime.agent_for_conversation(conv, project, model, user_context=user_context))
            if index == 4:
                storage.resource_versions.publish(None)
                agents[-1] = await runtime.agent_for_conversation(conv, project, model, user_context=user_context)
            elif index == 5:
                storage.resource_versions.publish("alice")
                agents[-1] = await runtime.agent_for_conversation(conv, project, model, user_context=user_context)

        assert all(agent is not baseline for agent in agents)
        assert len({id(agent) for agent in [baseline, explicitly_selected, restored_with_default_fallback, *agents]}) == 9

    asyncio.run(run())


def test_mcp_build_failure_is_not_cached_and_build_lock_is_released(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    attempts = []

    async def build(*args, **kwargs):
        agent = SimpleNamespace(melonclaw_mcp_failed=True)
        attempts.append(agent)
        return agent

    monkeypatch.setattr(runtime_module, "build_research_agent", build)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=SimpleNamespace(
            resource_versions=ResourceVersions(),
            list_visible_mcp_rows=AsyncMock(return_value=[]),
            effective_environment=AsyncMock(return_value={}),
        ),
        checkpointer=object(), memory_store=object(), memory_service=object(),
    )
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="default"))
    conversation = {"id": str(uuid4()), "user_id": "alice"}
    context = SimpleNamespace(user_id="alice", tenant_id="tenant")

    async def run():
        first = await runtime.agent_for_conversation(conversation, None, user_context=context)
        second = await runtime.agent_for_conversation(conversation, None, user_context=context)
        assert first is not second
        assert len(attempts) == 2
        assert runtime.agent_build_locks == {}
        assert runtime.workspace_agents == {}

    asyncio.run(run())


def test_cancelled_agent_build_releases_lock_and_does_not_cache(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    started = asyncio.Event()

    async def build(*args, **kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(runtime_module, "build_research_agent", build)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=SimpleNamespace(
            resource_versions=ResourceVersions(),
            list_visible_mcp_rows=AsyncMock(return_value=[]),
            effective_environment=AsyncMock(return_value={}),
        ),
        checkpointer=object(), memory_store=object(), memory_service=object(),
    )
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="default"))

    async def run():
        task = asyncio.create_task(runtime.agent_for_conversation(
            {"id": str(uuid4()), "user_id": "alice"}, None,
            user_context=SimpleNamespace(user_id="alice", tenant_id="tenant"),
        ))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.agent_build_locks == {}
        assert runtime.workspace_agents == {}

    import pytest

    asyncio.run(run())


def test_agent_build_exception_releases_lock_and_next_request_can_build(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    async def broken(*args, **kwargs):
        raise RuntimeError("fixture build failure")

    monkeypatch.setattr(runtime_module, "build_research_agent", broken)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=SimpleNamespace(
            resource_versions=ResourceVersions(),
            list_visible_mcp_rows=AsyncMock(return_value=[]),
            effective_environment=AsyncMock(return_value={}),
        ),
        checkpointer=object(), memory_store=object(), memory_service=object(),
    )
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="default"))
    conversation = {"id": str(uuid4()), "user_id": "alice"}
    context = SimpleNamespace(user_id="alice", tenant_id="tenant")

    async def run():
        with pytest.raises(RuntimeError, match="fixture build failure"):
            await runtime.agent_for_conversation(conversation, None, user_context=context)
        assert runtime.agent_build_locks == {}
        monkeypatch.setattr(
            runtime_module, "build_research_agent",
            AsyncMock(return_value=SimpleNamespace(melonclaw_mcp_failed=False)),
        )
        result = await runtime.agent_for_conversation(conversation, None, user_context=context)
        assert result in runtime.workspace_agents.values()

    import pytest

    asyncio.run(run())


def test_mcp_tool_cache_reused_across_conversations_and_invalidated_on_global_change(monkeypatch, tmp_path):
    """相同 MCP 配置在新会话中复用发现结果，全局版本变化后重新发现。"""
    from unittest.mock import AsyncMock

    from melonclaw.services.mcp_tool_cache import McpToolCache
    from melonclaw.tool.tools import McpDiscoveryResult

    mcp_row = {
        "id": str(uuid4()),
        "slug": "demo",
        "scope": "global",
        "enabled": True,
        "user_enabled": True,
        "transport": "http",
        "url": "https://demo.test/mcp",
        "headers": {},
        "env": {},
        "command": None,
        "args": [],
        "tool_allowlist": None,
    }
    discover_calls = 0
    seen_discoveries = []

    async def fake_discover(servers, allowlists):
        nonlocal discover_calls
        discover_calls += 1
        return McpDiscoveryResult(catalog={"demo": (f"mcp__demo__tool_{discover_calls}",)})

    async def fake_build(*args, mcp_discovery=None, **kwargs):
        seen_discoveries.append(mcp_discovery)
        return SimpleNamespace(melonclaw_mcp_failed=bool(mcp_discovery and mcp_discovery.transient_failures))

    monkeypatch.setattr(runtime_module, "build_research_agent", fake_build)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    versions = ResourceVersions()
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=SimpleNamespace(
            resource_versions=versions,
            list_visible_mcp_rows=AsyncMock(return_value=[mcp_row]),
            effective_environment=AsyncMock(return_value={}),
        ),
        checkpointer=object(),
        memory_store=object(),
        memory_service=object(),
        mcp_tool_cache=McpToolCache(discover=fake_discover),
    )
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="default"))
    context = SimpleNamespace(user_id="alice", tenant_id="tenant")

    async def run():
        conv1 = {"id": str(uuid4()), "user_id": "alice"}
        conv2 = {"id": str(uuid4()), "user_id": "alice"}
        await runtime.agent_for_conversation(conv1, None, user_context=context)
        await runtime.agent_for_conversation(conv2, None, user_context=context)
        assert discover_calls == 1
        assert seen_discoveries[0] is seen_discoveries[1]

        # 全局资源版本推进后，下一次构建重新发现 MCP
        versions.publish(None)
        conv3 = {"id": str(uuid4()), "user_id": "alice"}
        await runtime.agent_for_conversation(conv3, None, user_context=context)
        assert discover_calls == 2
        assert seen_discoveries[2] is not seen_discoveries[0]

    asyncio.run(run())


def test_deterministic_mcp_config_error_allows_agent_caching(monkeypatch, tmp_path):
    """MCP 环境变量无法解析属于确定性配置错误，不阻止 Agent 缓存；连接失败才跳过缓存。"""
    from unittest.mock import AsyncMock

    from melonclaw.services.mcp_tool_cache import McpToolCache
    from melonclaw.tool.tools import McpDiscoveryResult, build_mcp_tools

    bad_env_row = {
        "id": str(uuid4()),
        "slug": "missing_env",
        "scope": "global",
        "enabled": True,
        "user_enabled": True,
        "transport": "http",
        "url": "https://missing.test/mcp",
        "headers": {"Authorization": {"type": "reference", "env": "NONEXISTENT_MCP_TEST_ENV_VAR"}},
        "env": {},
        "command": None,
        "args": [],
        "tool_allowlist": None,
    }
    build_calls = 0

    async def fake_build(*args, mcp_servers=None, mcp_tool_allowlists=None, mcp_discovery=None, **kwargs):
        nonlocal build_calls
        build_calls += 1
        mcp_tools = await build_mcp_tools(mcp_servers, mcp_tool_allowlists, mcp_discovery)
        has_transient = any(
            getattr(tool, "metadata", None) and tool.metadata.get("mcp_transient_failures")
            for tool in mcp_tools
        )
        return SimpleNamespace(melonclaw_mcp_failed=has_transient, mcp_tools=mcp_tools)

    monkeypatch.setattr(runtime_module, "build_research_agent", fake_build)
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=SimpleNamespace(
            resource_versions=ResourceVersions(),
            list_visible_mcp_rows=AsyncMock(return_value=[bad_env_row]),
            effective_environment=AsyncMock(return_value={}),
        ),
        checkpointer=object(),
        memory_store=object(),
        memory_service=object(),
        mcp_tool_cache=McpToolCache(),
    )
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="default"))
    conv = {"id": str(uuid4()), "user_id": "alice"}
    context = SimpleNamespace(user_id="alice", tenant_id="tenant")

    async def run():
        first = await runtime.agent_for_conversation(conv, None, user_context=context)
        second = await runtime.agent_for_conversation(conv, None, user_context=context)
        assert first is second
        assert build_calls == 1
        assert first.melonclaw_mcp_failed is False
        catalog = next(t for t in first.mcp_tools if t.name == "list_mcp_tools")
        assert catalog.invoke({})["failed_server_count"] == 1

        # 对比：临时连接失败仍阻止 Agent 缓存
        async def transient_discover(servers, allowlists):
            return McpDiscoveryResult(
                failures={"flaky": "连接超时"},
                transient_failures=frozenset({"flaky"}),
            )

        runtime.mcp_tool_cache = McpToolCache(discover=transient_discover)
        runtime.storage.resource_versions.publish(None)
        third = await runtime.agent_for_conversation(conv, None, user_context=context)
        fourth = await runtime.agent_for_conversation(conv, None, user_context=context)
        assert third is not fourth
        assert third.melonclaw_mcp_failed is True

    asyncio.run(run())


def test_warmup_shared_mcp_cache_populates_cache_for_first_conversation(monkeypatch, tmp_path):
    """Web 启动后台预热共享 MCP 缓存后，首个新会话直接命中且预热异常不中断启动。"""
    from unittest.mock import AsyncMock

    from melonclaw.services.mcp_tool_cache import McpToolCache
    from melonclaw.tool.tools import McpDiscoveryResult

    shared_row = {
        "id": str(uuid4()),
        "slug": "shared",
        "scope": "global",
        "enabled": True,
        "user_enabled": True,
        "transport": "http",
        "url": "https://shared.test/mcp",
        "headers": {},
        "env": {},
        "command": None,
        "args": [],
        "tool_allowlist": None,
    }
    discover_calls = 0

    async def fake_discover(servers, allowlists):
        nonlocal discover_calls
        discover_calls += 1
        return McpDiscoveryResult(catalog={"shared": ("mcp__shared__ping",)})

    monkeypatch.setattr(
        runtime_module,
        "build_research_agent",
        AsyncMock(return_value=SimpleNamespace(melonclaw_mcp_failed=False)),
    )
    monkeypatch.setattr(runtime_module, "skill_snapshot", AsyncMock(return_value=("rev", (), [])))
    runtime = ChatRuntime(
        settings=SimpleNamespace(data_root=tmp_path, workspace_root=tmp_path, agent_cache_entries=32),
        storage=SimpleNamespace(
            resource_versions=ResourceVersions(),
            list_visible_mcp_rows=AsyncMock(return_value=[shared_row]),
            effective_environment=AsyncMock(return_value={}),
        ),
        checkpointer=object(),
        memory_store=object(),
        memory_service=object(),
        mcp_tool_cache=McpToolCache(discover=fake_discover),
    )
    runtime.resolve_model = AsyncMock(return_value=SimpleNamespace(profile_id="default"))
    context = SimpleNamespace(user_id="alice", tenant_id="tenant")

    async def run():
        await runtime.warmup_shared_mcp_cache()
        assert discover_calls == 1

        # 重启后的首个新会话直接命中预热缓存，不重复发现
        await runtime.agent_for_conversation(
            {"id": str(uuid4()), "user_id": "alice"}, None, user_context=context,
        )
        assert discover_calls == 1

        # 预热异常时静默降级，不向上抛出
        runtime.storage.list_visible_mcp_rows = AsyncMock(side_effect=RuntimeError("db blip"))
        await runtime.warmup_shared_mcp_cache()

    asyncio.run(run())
