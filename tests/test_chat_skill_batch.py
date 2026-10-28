"""批量同步的更新、合集去重、失败隔离与审批边界。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
import test_chat_skill_install
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_skill_import import VALID_SKILL_MD, make_zip
from test_skill_refresh import ToolModel

from melonclaw.core.agent import AgentContext
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS
from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
from melonclaw.tool.skill_install import build_skill_install_tools

installation = test_chat_skill_install.installation

URL = "https://skills.example/download?name=one"


def archive(name="my-skill", body=""):
    return make_zip({"SKILL.md": (VALID_SKILL_MD.replace("my-skill", name) + body).encode()})


def test_update_preserves_identity_and_disabled_state_and_skips_identical(installation):
    _, provider, storage, context, root, _ = installation

    async def run():
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(return_value=archive())):
            first = await provider.prepare_batch(context, source_urls=[URL], enable=False)
            assert first["items"][0]["operation"] == "install"
            assert (await provider.confirm_batch(context, first["installations"]))["status"] == "completed"
            row = storage.created[0]
            identity, version = row["id"], row["version"]
            same = await provider.prepare_batch(context, source_urls=[URL])
            assert same["items"][0]["status"] == "unchanged"
            assert same["installations"] == []
            assert row["version"] == version
            single = await provider.prepare(context, github_url=URL)
            assert single["status"] == "unchanged" and "installation" not in single
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(return_value=archive(body="更新正文"))):
            updated = await provider.prepare_batch(context, source_urls=[URL])
            assert updated["items"][0]["operation"] == "update"
            assert updated["installations"][0]["enable"] is False
            result = await provider.confirm_batch(context, updated["installations"])
            assert result["items"][0]["status"] == "updated"
            assert len(storage.created) == 1 and row["id"] == identity
            assert row["version"] == version + 1 and row["enabled"] is False
            assert "更新正文" in (root / "skills/users/u1/my-skill/SKILL.md").read_text()

    asyncio.run(run())


def test_bundle_is_expanded_deduplicated_and_failures_remain_visible(installation):
    _, provider, storage, context, _, _ = installation
    bundle = make_zip({"SKILL.md": b"untrusted instructions", "skills/one.zip": archive(),
                       "skills/two.zip": archive("other-skill"), "skills/bad.zip": make_zip({"README.md": b"bad"})})

    async def download(url):
        if "bundle" in url:
            return bundle
        if "error" in url:
            raise ValueError("do not echo sensitive errors")
        return archive()

    async def run():
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", download):
            prepared = await provider.prepare_batch(context, source_urls=[URL + "bundle", URL, URL + "error"])
            assert len(prepared["items"]) == 5
            assert len(prepared["installations"]) == 2
            assert {item["status"] for item in prepared["items"]} == {"prepared", "duplicate", "error"}
            assert "do not echo" not in str(prepared)
            result = await provider.confirm_batch(context, prepared["installations"])
            assert result["status"] == "completed"
            assert {row["name"] for row in storage.created} == {"my-skill", "other-skill"}

    asyncio.run(run())


def test_batch_conflicts_never_silently_pick_source(installation):
    _, provider, storage, context, _, _ = installation

    async def run():
        download = AsyncMock(side_effect=[archive(body="one"), archive(body="two")])
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", download):
            result = await provider.prepare_batch(context, source_urls=[URL, URL + "other"])
        assert not result["installations"]
        assert all(item["status"] == "conflict" for item in result["items"])
        assert not storage.created

    asyncio.run(run())


def test_partial_confirmation_rechecks_version_and_keeps_old_content(installation):
    _, provider, storage, context, root, _ = installation

    async def run():
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(return_value=archive())):
            first = await provider.prepare_batch(context, source_urls=[URL])
            await provider.confirm_batch(context, first["installations"])
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(side_effect=[archive(body="updated"), archive("new-skill")])):
            pending = await provider.prepare_batch(context, source_urls=[URL, URL + "new"])
        storage.created[0]["version"] += 1
        result = await provider.confirm_batch(context, pending["installations"])
        assert result["status"] == "partial"
        assert [item["status"] for item in result["items"]] == ["error", "installed"]
        assert "updated" not in (root / "skills/users/u1/my-skill/SKILL.md").read_text()

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_batch_real_graph_requires_approval(installation, decision):
    _, provider, storage, context, root, _ = installation

    async def run():
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(return_value=archive())):
            prepared = await provider.prepare_batch(context, source_urls=[URL])
        assert not storage.created
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[{
            "name": "confirm_skill_batch", "id": "confirm", "args": {"installations": prepared["installations"]},
        }]), AIMessage(content="完成")])
        tools = build_skill_install_tools(provider)
        assert not {tool.name for tool in tools} & set(INTERPRETER_PTC_TOOLS)
        graph = create_deep_agent(model=model, tools=tools,
            backend=FilesystemBackend(root_dir=root / "workspace", virtual_mode=True),
            context_schema=AgentContext, interrupt_on=SENSITIVE_TOOL_INTERRUPTS, checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": context.conversation_id}}
        result = await graph.ainvoke({"messages": [{"role": "user", "content": "安装技能"}]}, config, context=context)
        assert result["__interrupt__"] and not storage.created
        await graph.ainvoke(Command(resume={"decisions": [{"type": decision}]}), config, context=context)
        assert bool(storage.created) == (decision == "approve")

    asyncio.run(run())


def test_batch_cannot_cross_user_scope(installation):
    _, provider, storage, context, _, _ = installation

    async def run():
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(return_value=archive())):
            prepared = await provider.prepare_batch(context, source_urls=[URL])
        result = await provider.confirm_batch(replace(context, user_id="other"), prepared["installations"])
        assert result["status"] == "error" and not storage.created

    asyncio.run(run())


def test_batch_deadline_keeps_completed_sources_and_cancels_pending(installation, monkeypatch):
    from melonclaw.services import skill_install

    _, provider, storage, context, _, _ = installation
    cancelled = False

    async def download(url):
        nonlocal cancelled
        if url.endswith("stalled"):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True
        return archive()

    async def run():
        monkeypatch.setattr(skill_install, "BATCH_PREPARE_SECONDS", 0.05)
        with patch("melonclaw.services.chat.fetch_remote_skill_archive", download):
            result = await provider.prepare_batch(context, source_urls=[URL, URL + "stalled"])
        assert len(result["installations"]) == 1
        assert [item["status"] for item in result["items"]] == ["prepared", "error"]
        assert cancelled and not storage.created

    asyncio.run(run())
