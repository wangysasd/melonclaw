"""聊天安装复用真实文件导入：身份、审批清单、附件、HITL 与索引。"""

import asyncio
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_skill_import import VALID_SKILL_MD, FakeStorage, make_zip
from test_skill_refresh import ToolModel

from melonclaw.core.agent import AgentContext
from melonclaw.core.config import Settings
from melonclaw.core.hitl import SENSITIVE_TOOL_INTERRUPTS
from melonclaw.parsers.validation import AttachmentValidationError, validate_attachment
from melonclaw.services.chat import ChatService
from melonclaw.services.runtime import ChatRuntime
from melonclaw.services.skill_import import SkillImportError
from melonclaw.services.skill_index import reindex_skills_from_disk
from melonclaw.services.skill_state import evaluate_skills
from melonclaw.services.skills import SkillCatalog, SkillRoot
from melonclaw.tool.skill_install import build_skill_install_tools


@pytest.fixture
def installation(tmp_path):
    storage = FakeStorage()
    conversation_id = uuid4()
    context = AgentContext(user_id="u1", tenant_id="tenant", tenant_name="Tenant",
                           conversation_id=str(conversation_id))
    user = SimpleNamespace(user_id="u1", tenant_id="tenant", tenant_role="member")
    storage.get_user_context = AsyncMock(return_value=user)
    storage.get_conversation = AsyncMock(return_value={"id": str(conversation_id), "project_id": None})
    runtime = ChatRuntime(settings=Settings(workspace_root=tmp_path / "workspace", data_root=tmp_path), storage=storage, checkpointer=InMemorySaver())
    runtime.require_ready = lambda: storage
    chat = ChatService(runtime)
    # Keep both content and workspace isolated from developer configuration.
    archive = make_zip({"SKILL.md": VALID_SKILL_MD.encode()})
    path = tmp_path / "blob"
    path.write_bytes(archive)
    record = {"kind": "archive", "status": "attached", "size_bytes": len(archive),
              "sha256": hashlib.sha256(archive).hexdigest()}
    chat.attachments.content_path = AsyncMock(return_value=(path, record))
    return chat, runtime.skill_install_provider, storage, context, tmp_path, record


def test_tools_do_not_expose_identity_or_runtime(installation):
    _, provider, _, _, _, _ = installation
    for tool in build_skill_install_tools(provider):
        schema = tool.tool_call_schema.model_json_schema()
        properties = schema["properties"]
        assert not {"user_id", "tenant_id", "conversation_id", "runtime", "path"} & properties.keys()
    assert SENSITIVE_TOOL_INTERRUPTS["confirm_skill_install"]["allowed_decisions"] == ["approve", "reject"]
    from melonclaw.core.interpreter import INTERPRETER_PTC_TOOLS
    assert "confirm_skill_install" not in INTERPRETER_PTC_TOOLS


def test_attachment_install_and_duplicate_confirmation(installation):
    chat, provider, storage, context, root, _ = installation

    async def run():
        identifier = uuid4()
        prepared = await provider.prepare(context, attachment_id=str(identifier))
        assert prepared["status"] == "prepared"
        assert not storage.created
        chat.attachments.content_path.assert_awaited_once_with(
            identifier, "u1", project_id=None, conversation_id=UUID(context.conversation_id),
        )
        result = await provider.confirm(context, prepared["installation"])
        assert result["status"] == "installed"
        assert result["enabled"] is True
        assert storage.created[0]["scope"] == "user"
        assert (root / "skills/users/u1/my-skill/SKILL.md").is_file()
        again = await provider.confirm(context, prepared["installation"])
        assert again["status"] == "error"
        assert len(storage.created) == 1
    asyncio.run(run())


@pytest.mark.parametrize("field,value", [
    ("scope", "global"), ("name", "other"), ("enable", False),
    ("source_url", "https://github.com/other/repo"), ("content_hash", "0" * 64),
])
def test_confirmation_cannot_change_preview(installation, field, value):
    _, provider, storage, context, _, _ = installation

    async def run():
        prepared = await provider.prepare(context, attachment_id=str(uuid4()))
        changed = {**prepared["installation"], field: value}
        assert (await provider.confirm(context, changed))["status"] == "error"
        assert not storage.created
    asyncio.run(run())


def test_user_conversation_role_and_entrypoint_rechecked(installation):
    chat, provider, storage, context, _, _ = installation

    async def run():
        prepared = await provider.prepare(context, attachment_id=str(uuid4()))
        confirmation = prepared["installation"]
        with pytest.raises(SkillImportError, match="入口"):
            await chat.skill_imports.confirm(user_id="u1", draft_id=confirmation["draft_id"], storage=storage)
        # Valid other user/conversation still cannot use this draft.
        storage.get_user_context.return_value = SimpleNamespace(user_id="u2", tenant_id="tenant", tenant_role="member")
        assert (await provider.confirm(replace(context, user_id="u2"), confirmation))["status"] == "error"
        storage.get_user_context.return_value = SimpleNamespace(user_id="u1", tenant_id="tenant", tenant_role="member")
        other = str(uuid4())
        storage.get_conversation.return_value = {"id": other, "project_id": None}
        assert (await provider.confirm(replace(context, conversation_id=other), confirmation))["status"] == "error"
        storage.get_conversation.return_value = {"id": context.conversation_id, "project_id": None}
        storage.get_user_context.return_value.tenant_role = "admin"
        assert (await provider.confirm(context, confirmation))["status"] == "error"
        assert not storage.created
    asyncio.run(run())


def test_missing_attachment_and_changed_bytes_are_rejected(installation):
    chat, provider, storage, context, root, record = installation
    from melonclaw.repository import AttachmentNotFoundError

    async def run():
        record["status"] = "staged"
        assert (await provider.prepare(context, attachment_id=str(uuid4())))["status"] == "error"
        record["status"] = "attached"
        (root / "blob").write_bytes(b"changed")
        assert (await provider.prepare(context, attachment_id=str(uuid4())))["status"] == "error"
        chat.attachments.content_path.side_effect = AttachmentNotFoundError()
        assert (await provider.prepare(context, attachment_id=str(uuid4())))["status"] == "error"
        assert not storage.created
    asyncio.run(run())


def test_github_uses_existing_download_pin_and_import_path(installation):
    _, provider, storage, context, _, _ = installation
    source = SimpleNamespace(url="https://codeload.github.com/o/r/zip/" + "a" * 40,
                             subpath=None, source_url="https://github.com/o/r", ref="a" * 40)
    archive = make_zip({"SKILL.md": VALID_SKILL_MD.encode()})

    async def run():
        with patch("melonclaw.services.chat.pin_remote_source", AsyncMock(return_value=source)) as pin, \
             patch("melonclaw.services.chat.fetch_remote_skill_archive", AsyncMock(return_value=archive)) as fetch:
            prepared = await provider.prepare(context, github_url=source.source_url, enable=False)
            pin.assert_awaited_once()
            fetch.assert_awaited_once_with(source.url)
            assert prepared["source_ref"] == "a" * 40
            result = await provider.confirm(context, prepared["installation"])
            assert result["status"] == "installed" and result["enabled"] is False
            assert storage.created[0]["source_type"] == "remote"
            assert storage.created[0]["enabled"] is False
    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_real_agent_graph_requires_approval_and_injects_context(installation, decision):
    _, provider, storage, context, root, _ = installation

    async def run():
        prepared = await provider.prepare(context, attachment_id=str(uuid4()))
        model = ToolModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "confirm_skill_install", "id": "install-call",
                                               "args": {"installation": prepared["installation"]}}]),
            AIMessage(content="done"),
        ])
        agent = create_deep_agent(
            model=model, tools=build_skill_install_tools(provider),
            backend=FilesystemBackend(root_dir=root / "workspace", virtual_mode=True),
            context_schema=AgentContext, interrupt_on=SENSITIVE_TOOL_INTERRUPTS,
            checkpointer=InMemorySaver(),
        )
        config = {"configurable": {"thread_id": context.conversation_id}}
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "安装 Skill"}]}, config, context=context)
        assert result["__interrupt__"]
        assert not storage.created
        result = await agent.ainvoke(Command(resume={"decisions": [{"type": decision}]}), config, context=context)
        assert bool(storage.created) == (decision == "approve")
        if decision == "approve":
            tool_message = next(message for message in result["messages"] if message.type == "tool")
            assert json.loads(tool_message.content)["status"] == "installed"
    asyncio.run(run())


def test_zip_attachment_validation(tmp_path):
    path = tmp_path / "blob"
    path.write_bytes(make_zip({"SKILL.md": VALID_SKILL_MD.encode()}))
    assert validate_attachment(path, "skill.zip", "application/zip")[2] == "archive"
    assert validate_attachment(path, "skill.zip", "application/x-zip-compressed")[2] == "archive"
    for entries in ({"../escape": b"bad"}, {"/absolute": b"bad"}):
        path.write_bytes(make_zip(entries))
        with pytest.raises(AttachmentValidationError):
            validate_attachment(path, "skill.zip", "application/zip")
    path.write_bytes(b"not zip")
    with pytest.raises(AttachmentValidationError):
        validate_attachment(path, "skill.zip", "application/zip")


def test_tutorial_is_indexed_enabled_and_loadable(tmp_path):
    source = Path(__file__).resolve().parents[1] / ".data/skills/shared/melonclaw-tutorial"
    root = tmp_path / "skills/shared"
    shutil.copytree(source, root / source.name)
    from test_skill_index import FakeStorage as IndexStorage
    storage = IndexStorage(users=["admin"])
    asyncio.run(reindex_skills_from_disk(storage, tmp_path))
    row = storage.rows[source.name]
    # Real repository's default for newly indexed builtins is enabled=True.
    row.update(id=uuid4(), enabled=True, user_enabled=None, status="ready", version=1,
               source_url="", source_ref="", content_hash="")
    catalog = SkillCatalog(roots=(SkillRoot("global", "/skills/", root),))
    states = evaluate_skills([row], catalog, tmp_path / "skills")
    assert states[0].effective_enabled
    assert states[0].definition.id == "melonclaw-tutorial"
