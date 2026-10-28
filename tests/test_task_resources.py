"""当前资源、只读路径和后台真实图的 Skill/MCP/有界收尾回归。"""

import asyncio
import json
import shutil
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from test_mcp_service import make_row as mcp_row
from test_task_agent import Model, build, settings_for

from melonclaw.backend.task_skills import TaskSkillBackend
from melonclaw.core.agent import AgentContext
from melonclaw.core.task_policy import TaskRunControl
from melonclaw.repository.resource_versions import ResourceVersions
from melonclaw.services.task_execution import execute_task_agent
from melonclaw.services.task_resources import load_task_resources


def skill(tmp_path, name="finance", *, scope="global", enabled=True, user_enabled=None, body="初版规则"):
    relative = f"shared/{name}" if scope == "global" else f"users/u/{name}"
    directory = tmp_path / "data/skills" / relative
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: 财务分析工作流\n---\n{body}\n")
    (directory / "reference.md").write_text("本轮引用资料")
    row = dict(id=str(uuid4()), name=name, scope=scope, enabled=enabled,
               user_enabled=user_enabled, created_by="u", storage_path=relative,
               status="ready", version=1, content_hash="")
    return directory, row


class Resources:
    def __init__(self, rows=(), servers=()):
        self.rows = list(rows)
        self.servers = list(servers)
        self.resource_versions = ResourceVersions()

    async def list_visible_skill_rows(self, user_id, *, include_disabled):
        assert user_id == "u" and include_disabled
        return self.rows

    async def list_visible_mcp_rows(self, user_id):
        assert user_id == "u"
        return self.servers


async def execute(agent, control, settings, instruction="执行研究"):
    return await execute_task_agent(
        agent, run_id=uuid4(), instruction=instruction,
        context=AgentContext(user_id="u", tenant_id="t", tenant_name="租户"),
        control=control, settings=settings,
    )


def test_resources_follow_current_state_without_content_copies(tmp_path):
    async def run():
        global_dir, shared = skill(tmp_path)
        own_dir, own = skill(tmp_path, scope="user")
        _, disabled = skill(tmp_path, "disabled", enabled=False)
        _, personal_off = skill(tmp_path, "personal-off", user_enabled=False)
        storage = Resources([shared, own, disabled, personal_off], [mcp_row(scope="global"), mcp_row(enabled=False)])
        settings = settings_for(tmp_path)
        current = await load_task_resources(storage, settings, "u")
        assert current.skill_mounts == (("/skills-user/finance/", own_dir),)
        assert not current.mcp_servers  # 个人停用项不回退到同名共享 MCP。
        own["enabled"] = False
        current = await load_task_resources(storage, settings, "u")
        assert current.skill_mounts == (("/skills/finance/", global_dir),)
        backend = TaskSkillBackend(current.skill_mounts)
        (global_dir / "reference.md").write_text("当前新版")
        assert backend.read("/skills/finance/reference.md").file_data["content"] == "当前新版"
        shutil.rmtree(global_dir)
        assert backend.read("/skills/finance/reference.md").error
        assert not (await load_task_resources(storage, settings, "u")).skill_mounts
        assert not (settings.data_root / "skills/.snapshots").exists()
        storage.servers.pop()
        storage.servers[0]["tool_allowlist"] = ["query"]
        current = await load_task_resources(storage, settings, "u")
        assert set(current.mcp_servers) == {"svc"}
        assert current.mcp_tool_allowlists == {"svc": ("query",)}
    asyncio.run(run())


def test_resource_changes_during_preparation_are_reloaded(tmp_path):
    async def run():
        _, row = skill(tmp_path)
        storage = Resources([row])
        original = storage.list_visible_mcp_rows
        calls = 0

        async def changed(user_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                row["enabled"] = False
                storage.resource_versions.publish("u")
            return await original(user_id)

        storage.list_visible_mcp_rows = changed
        current = await load_task_resources(storage, settings_for(tmp_path), "u")
        assert current.skill_mounts == () and calls == 2
    asyncio.run(run())


def test_skill_backend_only_reads_authorized_regular_files(tmp_path):
    directory, _ = skill(tmp_path)
    outside = tmp_path / "private.txt"
    outside.write_text("不应读取的内容")
    backend = TaskSkillBackend((("/skills/finance/", directory),))
    assert backend.ls("/skills/").entries == [{"path": "/skills/finance/", "is_dir": True}]
    assert backend.read("/skills/finance/SKILL.md", offset=1, limit=1).file_data["content"] == "name: finance\n"
    assert backend.download_files(["/skills/finance/SKILL.md"])[0].content.startswith(b"---")
    for path in ["/etc/passwd", "/skills/other/SKILL.md", "/skills/finance/../../private.txt", "~/private.txt"]:
        response = backend.read(path)
        assert response.error and str(tmp_path) not in response.error
    (directory / "link.txt").symlink_to(outside)
    (directory / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    assert backend.read("/skills/finance/link.txt").error
    assert backend.read("/skills/finance/linked-dir/private.txt").error
    assert not any("link" in item["path"] for item in backend.ls("/skills/finance/").entries)
    assert backend.write("/skills/finance/new.txt", "拒绝").error
    assert backend.edit("/skills/finance/reference.md", "本轮", "旧版").error
    assert backend.delete("/skills/finance/").error
    assert not hasattr(backend, "execute")
    saved = directory.with_name("saved")
    directory.rename(saved)
    directory.symlink_to(tmp_path, target_is_directory=True)
    assert backend.read("/skills/finance/private.txt").error


@pytest.mark.parametrize("delegate", [False, True])
def test_background_main_and_child_read_skill_and_call_current_mcp(monkeypatch, tmp_path, delegate):
    calls, bindings = [], []
    directory, row = skill(tmp_path)

    @tool
    async def query_finance(symbol: str) -> str:
        """Query offline financial data."""
        calls.append(symbol)
        return "本轮财务查询结果"

    @tool
    async def hidden_write() -> str:
        """Unapproved remote write."""
        raise AssertionError("不得调用白名单外工具")

    class Client:
        def __init__(self, servers):
            assert set(servers) == {"finance", "broken"}

        async def get_tools(self, *, server_name):
            if server_name == "broken":
                raise ConnectionError("不得泄露远端原始错误")
            return [query_finance, hidden_write]

    monkeypatch.setattr("melonclaw.tool.tools.MultiServerMCPClient", Client)

    class Research(Model):
        def bind_tools(self, tools, **kwargs):
            return self.bind(visible={item.name for item in tools})

        async def _agenerate(self, messages, *, visible, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            results = [m for m in messages[messages.index(user) + 1:] if isinstance(m, ToolMessage)]
            bindings.append((user.content, visible))
            if user.content == "parent":
                if results:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content="汇总本轮查询结果"))])
                name, args = "task", {"description": "child", "subagent_type": "general-purpose"}
            elif not results:
                assert "财务分析工作流" in str(messages[0].content)
                name, args = "read_file", {"file_path": "/skills/finance/SKILL.md"}
            elif len(results) == 1:
                assert "初版规则" in results[-1].content
                name, args = "read_file", {"file_path": "/skills/finance/reference.md"}
            elif len(results) == 2:
                assert "本轮引用资料" in results[-1].content
                name, args = "list_mcp_tools", {}
            elif len(results) == 3:
                catalog = json.loads(results[-1].content)
                assert catalog["failed_server_count"] == 1
                names = [n for server in catalog["servers"] for n in server["tools"]]
                assert len(names) == 1 and "hidden_write" not in names[0]
                assert names[0] not in visible
                name, args = "find_tools", {"tool_names": names}
            elif len(results) == 4:
                receipt = json.loads(results[-1].content)
                assert receipt["status"] == "selected"
                name, args = receipt["added_tools"][0], {"symbol": "EXAMPLE"}
                assert name in visible
            else:
                assert "本轮财务查询结果" in results[-1].content
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已按 Skill 完成本轮财务查询；跳过不可用服务。"))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="", tool_calls=[{"name": name, "args": args, "id": f"{user.content}-{len(results)}"}],
            ))])

    async def run():
        settings = settings_for(tmp_path)
        resources = await load_task_resources(Resources([row]), settings, "u")
        agent, control, _ = await build(
            monkeypatch, tmp_path, Research(responses=[]), settings=settings,
            skill_mounts=resources.skill_mounts,
            mcp_servers={"finance": {"transport": "http", "url": "https://offline.invalid/mcp"},
                         "broken": {"transport": "http", "url": "https://offline.invalid/broken"}},
            mcp_tool_allowlists={"finance": ("query_finance",)},
        )
        outcome = await execute(agent, control, settings, "parent" if delegate else "执行研究")
        assert outcome.status == "succeeded", outcome
        assert calls == ["EXAMPLE"]
        assert len(control._discoveries) == 1
        for user, visible in bindings:
            assert not visible & {"execute", "write_file", "ask_user", "configure_task", "eval", "hidden_write"}
            if user == "child":
                assert "task" not in visible and "complete_task" not in visible
        assert directory.is_dir() and not (settings.data_root / "skills/.snapshots").exists()
    asyncio.run(run())


def test_missing_tools_return_normal_success_without_selector_call(monkeypatch, tmp_path):
    async def run():
        model = Model(responses=[
            AIMessage(content="", tool_calls=[{"name": "find_tools", "args": {"query": "缺少的财务工具"}, "id": "find"}]),
            AIMessage(content="当前没有财务查询工具，本次跳过估值计算。"),
        ])
        agent, control, settings = await build(monkeypatch, tmp_path, model)
        outcome = await execute(agent, control, settings)
        assert outcome.status == "succeeded" and "跳过" in outcome.result_markdown
        assert control._model_calls == 2 and not control.goal_complete
    asyncio.run(run())


@pytest.mark.parametrize("budget,expected", [(TaskRunControl(3, 20), "model_limit"), (TaskRunControl(20, 2), "tool_limit")])
def test_repeated_discovery_is_stopped_by_run_budget(monkeypatch, tmp_path, budget, expected):
    class Loop(Model):
        async def _agenerate(self, messages, **kwargs):
            count = len([m for m in messages if isinstance(m, ToolMessage)])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[{
                "name": "find_tools", "args": {"query": "missing"}, "id": f"loop-{count}",
            }]))])

    async def run():
        agent, control, settings = await build(monkeypatch, tmp_path, Loop(responses=[]), control=budget)
        outcome = await execute(agent, control, settings)
        assert outcome.status == "failed" and outcome.error_code == expected, outcome
        assert len(control._discoveries) <= settings.tool_selection_max_requests
    asyncio.run(run())


def test_discovery_allowance_is_shared_with_child(monkeypatch, tmp_path):
    receipts = []

    class Delegate(Model):
        async def _agenerate(self, messages, **kwargs):
            user = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
            results = [m for m in messages[messages.index(user) + 1:] if isinstance(m, ToolMessage)]
            if not results:
                name, args = "find_tools", {"query": "missing"}
            elif results[-1].name == "find_tools":
                receipts.append(json.loads(results[-1].content)["status"])
                if user.content == "parent":
                    name, args = "task", {"description": "child", "subagent_type": "general-purpose"}
                else:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已无发现额度，跳过并结束。"))])
            else:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已说明缺失并结束。"))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[{
                "name": name, "args": args, "id": f"{user.content}-{len(results)}",
            }]))])

    async def run():
        settings = settings_for(tmp_path, tool_selection_max_requests=1)
        agent, control, _ = await build(monkeypatch, tmp_path, Delegate(responses=[]), settings=settings)
        outcome = await execute(agent, control, settings, "parent")
        assert outcome.status == "succeeded", outcome
        assert receipts == ["no_match", "exhausted"] and len(control._discoveries) == 1
    asyncio.run(run())


def test_selector_model_call_consumes_shared_budget(monkeypatch, tmp_path):
    selections = []

    @tool
    async def read_data() -> str:
        """Read offline data."""
        return "data"

    async def mcp(*args):
        read_data.metadata = {"mcp": True}
        return [read_data]

    monkeypatch.setattr("melonclaw.core.task_agent.build_mcp_tools", mcp)

    class Select(Model):
        def with_structured_output(self, schema, **kwargs):
            async def selected(messages):
                selections.append(messages[-1].content)
                return {"tools": ["read_data"]}
            return RunnableLambda(selected)

    async def run():
        agent, control, settings = await build(
            monkeypatch, tmp_path,
            Select(responses=[AIMessage(content="", tool_calls=[{"name": "find_tools", "args": {"query": "read data"}, "id": "find"}])]),
            control=TaskRunControl(2, 20),
        )
        outcome = await execute(agent, control, settings)
        assert selections and outcome.error_code == "model_limit", outcome
        assert control._model_calls == 2
    asyncio.run(run())


@pytest.mark.parametrize("stage", ["discovery", "tool"])
def test_mcp_cancellation_propagates(monkeypatch, tmp_path, stage):
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()

        @tool
        async def hanging_read() -> str:
            """Read that can be cancelled."""
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        class Client:
            def __init__(self, servers):
                pass

            async def get_tools(self, *, server_name):
                if stage == "discovery":
                    started.set()
                    try:
                        await asyncio.Future()
                    finally:
                        stopped.set()
                return [hanging_read]

        monkeypatch.setattr("melonclaw.tool.tools.MultiServerMCPClient", Client)
        from melonclaw.tool.tools import wrap_mcp_tool
        servers = {"offline": {"transport": "http", "url": "https://offline.invalid/mcp"}}
        name = wrap_mcp_tool("offline", hanging_read, servers["offline"]).name
        model = Model(responses=[AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": "read"}])])
        task = asyncio.create_task(build(monkeypatch, tmp_path, model, mcp_servers=servers))
        if stage == "tool":
            agent, control, settings = await task
            task = asyncio.create_task(execute(agent, control, settings))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        if stage == "discovery":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status == "cancelled"
        assert stopped.is_set()
    asyncio.run(run())
