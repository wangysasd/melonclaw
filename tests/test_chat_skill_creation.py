"""聊天生成复用真实暂存、发布与图审批，验证归属和内容完整性。"""

import asyncio
import json
import shutil
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_chat_skill_install import installation as installation
from test_skill_import import VALID_SKILL_MD
from test_skill_refresh import ToolModel

from melonclaw.core.agent import AgentContext
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS
from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
from melonclaw.services.skill_creation import MAX_GENERATED_BYTES, pack_generated_skill
from melonclaw.services.skill_import import SkillImportError
from melonclaw.services.skill_snapshot import skill_snapshot
from melonclaw.services.skills import SkillCatalog, SkillRoot
from melonclaw.tool.skill_install import build_skill_install_tools


@pytest.mark.parametrize("role,scope", [("member", "user"), ("admin", "global"), ("owner", "global")])
@pytest.mark.parametrize("enable", [True, False])
def test_creation_saves_files_and_index_for_role(installation, role, scope, enable):
    chat, provider, storage, context, root, _ = installation
    storage.get_user_context.return_value.tenant_role = role
    files = {"SKILL.md": VALID_SKILL_MD, "references/output.md": "输出格式\n"}

    async def run():
        prepared = await provider.prepare_creation(context, files=files, enable=enable)
        assert prepared["status"] == "prepared"
        assert prepared["scope"] == scope
        assert prepared["generated_files"] == files
        assert prepared["source_type"] == "generated"
        assert prepared["preview"]["body"] == VALID_SKILL_MD
        assert not storage.created
        chat.attachments.content_path.assert_not_awaited()
        result = await provider.confirm(context, prepared["installation"])
        assert result["status"] == "installed"
        assert result["enabled"] == enable
        row = storage.created[0]
        assert row["source_type"] == "generated"
        assert row["created_by"] == "u1" and row["status"] == "ready"
        assert row["source_ref"] == f"chat:{context.conversation_id}"
        expected = "shared/my-skill" if scope == "global" else "users/u1/my-skill"
        assert row["storage_path"] == expected
        for name, content in files.items():
            assert (root / "skills" / expected / name).read_text() == content
        assert (await provider.confirm(context, prepared["installation"]))["status"] == "error"
        # 下一轮运行使用同一有效状态与真实只读快照。
        catalog = SkillCatalog(roots=(SkillRoot(scope, "/skills/", (root / "skills" / expected).parent),))
        row["user_enabled"] = None
        storage.list_visible_skill_rows = AsyncMock(return_value=storage.created)
        _, directories, references = await skill_snapshot(storage, "u1", root, catalog)
        assert bool(references) == enable
        if enable:
            assert (directories[0][1] / "my-skill/SKILL.md").read_text() == VALID_SKILL_MD

    asyncio.run(run())


@pytest.mark.parametrize("path", [
    "../escape.md", "/tmp/escape.md", "references/../escape.md", "references\\escape.md",
    "scripts/run.py", "assets/file.png", "references/a.md/b.txt", "references/.env",
])
def test_generated_paths_are_restricted(path):
    with pytest.raises(SkillImportError):
        pack_generated_skill({"SKILL.md": VALID_SKILL_MD, path: "unsafe"})


@pytest.mark.parametrize("files", [
    {}, {"references/info.md": "no entry"}, {"SKILL.md": "a" * (MAX_GENERATED_BYTES + 1)},
    {"SKILL.md": "中" * (MAX_GENERATED_BYTES // 3 + 1)}, {"SKILL.md": "\x00"},
    {"SKILL.md": "\ud800"}, {"SKILL.md": 123},
    {"SKILL.md": VALID_SKILL_MD, **{f"references/{i}.txt": "x" for i in range(32)}},
])
def test_generated_limits_and_encoding(files):
    with pytest.raises(SkillImportError):
        pack_generated_skill(files)


def test_invalid_metadata_conflicts_and_modified_draft(installation):
    _, provider, storage, context, root, _ = installation

    async def run():
        assert (await provider.prepare_creation(context, files={"SKILL.md": "not a skill"}))["status"] == "error"
        first = await provider.prepare_creation(context, files={"SKILL.md": VALID_SKILL_MD})
        second = await provider.prepare_creation(context, files={"SKILL.md": VALID_SKILL_MD})
        assert (await provider.confirm(context, first["installation"]))["status"] == "installed"
        assert (await provider.confirm(context, second["installation"]))["status"] == "error"
        assert len(storage.created) == 1
        assert (await provider.prepare_creation(context, files={"SKILL.md": VALID_SKILL_MD}))["status"] == "error"
        new_md = VALID_SKILL_MD.replace("my-skill", "another-skill")
        prepared = await provider.prepare_creation(context, files={"SKILL.md": new_md})
        draft_root = root / "skills/tmp" / prepared["draft_id"]
        (draft_root / "extracted/another-skill/SKILL.md").write_text(new_md + "changed")
        assert (await provider.confirm(context, prepared["installation"]))["status"] == "error"
        assert len(storage.created) == 1
    asyncio.run(run())


def test_creation_revalidates_identity_expiry_and_approval(installation):
    _, provider, storage, context, root, _ = installation

    async def run():
        prepared = await provider.prepare_creation(context, files={"SKILL.md": VALID_SKILL_MD})
        confirmation = prepared["installation"]
        assert (await provider.confirm(context, {**confirmation, "enable": False}))["status"] == "error"
        storage.get_user_context.return_value.user_id = "u2"
        assert (await provider.confirm(replace(context, user_id="u2"), confirmation))["status"] == "error"
        storage.get_user_context.return_value.user_id = "u1"
        other = str(uuid4())
        storage.get_conversation.return_value = {"id": other, "project_id": None}
        assert (await provider.confirm(replace(context, conversation_id=other), confirmation))["status"] == "error"
        storage.get_conversation.return_value = {"id": context.conversation_id, "project_id": None}
        storage.get_user_context.return_value.tenant_role = "admin"
        assert (await provider.confirm(context, confirmation))["status"] == "error"
        storage.get_user_context.return_value.tenant_role = "member"
        manifest = root / "skills/tmp" / prepared["draft_id"] / "draft.json"
        data = json.loads(manifest.read_text())
        data["expires_at"] = time.time() - 1
        manifest.write_text(json.dumps(data))
        assert (await provider.confirm(context, confirmation))["status"] == "error"
        assert not storage.created
        assert (await provider.prepare_creation(None, files={"SKILL.md": VALID_SKILL_MD}))["status"] == "error"
    asyncio.run(run())


def test_failed_database_commit_restores_unpublished_state(installation):
    _, provider, storage, context, root, _ = installation

    async def run():
        prepared = await provider.prepare_creation(context, files={"SKILL.md": VALID_SKILL_MD})
        storage.update_skill_row = AsyncMock(side_effect=RuntimeError("private database details"))
        result = await provider.confirm(context, prepared["installation"])
        assert result["status"] == "error"
        assert "private database details" not in result["message"]
        assert not (root / "skills/users/u1/my-skill").exists()
        assert storage.deleted == [str(storage.created[0]["id"])]
        assert not list((root / "skills/.operations").iterdir())
    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_generation_tool_graph_injects_context_and_requires_approval(installation, decision):
    _, provider, storage, context, root, _ = installation
    tools = build_skill_install_tools(provider)
    creation = next(tool for tool in tools if tool.name == "prepare_skill_creation")
    assert set(creation.tool_call_schema.model_json_schema()["properties"]) == {"files", "enable"}
    assert "prepare_skill_creation" not in INTERPRETER_PTC_TOOLS

    async def run():
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "prepare_skill_creation", "id": "create-call",
                                               "args": {"files": {"SKILL.md": VALID_SKILL_MD}}}]),
            # 确认参数要用实际 prepare 结果，因此先执行图的准备阶段。
            AIMessage(content="preview"),
        ])
        agent = create_deep_agent(
            model=model, tools=tools,
            backend=FilesystemBackend(root_dir=root / "workspace", virtual_mode=True),
            context_schema=AgentContext, interrupt_on=SENSITIVE_TOOL_INTERRUPTS,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": context.conversation_id}}
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "把聊天生成 Skill"}]}, config, context=context)
        prepared = json.loads(next(message for message in result["messages"] if message.type == "tool").content)
        assert prepared["status"] == "prepared" and not storage.created
        model.responses = [
            AIMessage(content="", tool_calls=[{"name": "confirm_skill_install", "id": "save-call",
                                               "args": {"installation": prepared["installation"]}}]),
            AIMessage(content="done"),
        ]
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "保存"}]}, config, context=context)
        assert result["__interrupt__"] and not storage.created
        await agent.ainvoke(Command(resume={"decisions": [{"type": decision}]}), config, context=context)
        assert bool(storage.created) == (decision == "approve")
    asyncio.run(run())


def test_builtin_creator_is_discoverable_and_indexed(tmp_path):
    from test_skill_index import FakeStorage as IndexStorage

    from melonclaw.services.skill_index import reindex_skills_from_disk

    source = Path(__file__).resolve().parents[1] / ".data/skills/shared/skill-creator"
    root = tmp_path / "skills/shared"
    shutil.copytree(source, root / source.name)
    assert SkillCatalog(root).list()[0].id == "skill-creator"
    storage = IndexStorage(users=["admin"])
    asyncio.run(reindex_skills_from_disk(storage, tmp_path))
    assert storage.rows["skill-creator"]["scope"] == "global"
