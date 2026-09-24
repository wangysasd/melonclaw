"""普通会话加入项目时的文件搬迁与业务边界。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from melonclaw.repository import ConversationMoveError
from melonclaw.services.conversations import ConversationService
from melonclaw.storage.workspace_moves import (
    WorkspaceMoveConflictError,
    copy_conversation_workspace,
)


def test_workspace_copy_moves_registered_attachments_and_keeps_other_project_files(tmp_path):
    source = tmp_path / "conversations" / "conversation"
    destination = tmp_path / "projects" / "project"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    attachment_id = uuid4()
    (source / "notes.txt").write_text("会话文件", encoding="utf-8")
    (destination / "existing.txt").write_text("项目文件", encoding="utf-8")
    derived = source / ".attachments" / str(attachment_id) / "derived"
    derived.mkdir(parents=True)
    (derived / "index.md").write_text("附件内容", encoding="utf-8")
    orphan = source / ".attachments" / str(uuid4())
    orphan.mkdir()
    (orphan / "blob").write_bytes(b"orphan")

    copied = copy_conversation_workspace(source, destination, {attachment_id})

    assert (destination / "notes.txt").read_text(encoding="utf-8") == "会话文件"
    assert (destination / "existing.txt").read_text(encoding="utf-8") == "项目文件"
    assert (destination / ".attachments" / str(attachment_id) / "derived" / "index.md").read_text(encoding="utf-8") == "附件内容"
    assert not (destination / ".attachments" / orphan.name).exists()
    copied.remove_source()
    assert not source.exists()


def test_workspace_copy_rejects_conflicts_without_leaving_partial_files(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "first.txt").write_text("new", encoding="utf-8")
    (source / "last.txt").write_text("new", encoding="utf-8")
    (destination / "last.txt").write_text("old", encoding="utf-8")

    with pytest.raises(WorkspaceMoveConflictError, match="同名文件"):
        copy_conversation_workspace(source, destination, set())

    assert not (destination / "first.txt").exists()
    assert (destination / "last.txt").read_text(encoding="utf-8") == "old"
    assert source.is_dir()


class _Repository:
    def __init__(self, conversation_id, project_id, attachment_id):
        self.conversation_id = conversation_id
        self.project_id = project_id
        self.attachment_id = attachment_id
        self.conversation_project_id = None
        self.fail_move = False
        self.move_calls = 0
        self.released = False
        self.timestamp = datetime.now(UTC)

    async def get_user_context(self, user_id, tenant_id):
        return SimpleNamespace(user_id=user_id)

    async def try_advisory_lock(self, conversation_id):
        return object()

    async def release_advisory_lock(self, lock, conversation_id):
        self.released = True

    async def get_conversation(self, conversation_id, user_id):
        return {"id": str(conversation_id), "user_id": user_id, "project_id": self.conversation_project_id}

    async def get_project(self, project_id, user_id):
        return {"id": str(project_id), "workdir_path": f"projects/{project_id}"}

    async def get_incomplete_assistant(self, conversation_id, user_id):
        return None

    async def get_recovery_required_interaction(self, conversation_id, user_id):
        return None

    async def list_conversation_move_attachments(self, conversation_id, user_id):
        return [{
            "id": self.attachment_id,
            "updated_at": self.timestamp,
            "status": "attached",
            "parse_status": "processed",
        }]

    async def move_conversation_to_project(self, conversation_id, user_id, project_id, **kwargs):
        self.move_calls += 1
        if self.fail_move:
            raise ConversationMoveError("数据库拒绝移动。", "move_rejected")
        return {"id": str(conversation_id), "project_id": str(project_id)}


def _service(tmp_path: Path):
    conversation_id = uuid4()
    project_id = uuid4()
    attachment_id = uuid4()
    repository = _Repository(conversation_id, project_id, attachment_id)
    runtime = SimpleNamespace(
        require_ready=lambda: repository,
        storage=repository,
        settings=SimpleNamespace(attachment_project_max_bytes=1024),
        conversation_workspace_dir=lambda identifier: tmp_path / "conversations" / str(identifier),
        project_workspace_dir=lambda project: tmp_path / project["workdir_path"],
    )
    source = runtime.conversation_workspace_dir(conversation_id)
    destination = runtime.project_workspace_dir({"workdir_path": f"projects/{project_id}"})
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "notes.txt").write_text("work", encoding="utf-8")
    original = source / ".attachments" / str(attachment_id) / "original"
    original.mkdir(parents=True)
    (original / "blob").write_bytes(b"file")
    return ConversationService(runtime), repository, conversation_id, project_id, source, destination


def test_move_service_keeps_original_ids_and_files_when_repository_rejects(tmp_path):
    service, repository, conversation_id, project_id, source, destination = _service(tmp_path)
    repository.fail_move = True

    with pytest.raises(ConversationMoveError, match="数据库拒绝移动"):
        asyncio.run(service.move_conversation_to_project(conversation_id, project_id, "user-1"))

    assert source.joinpath("notes.txt").is_file()
    assert not destination.joinpath("notes.txt").exists()
    assert repository.released


def test_move_service_uses_project_workspace_after_success(tmp_path):
    service, repository, conversation_id, project_id, source, destination = _service(tmp_path)

    moved = asyncio.run(service.move_conversation_to_project(conversation_id, project_id, "user-1"))

    assert moved == {"id": str(conversation_id), "project_id": str(project_id)}
    assert destination.joinpath("notes.txt").read_text(encoding="utf-8") == "work"
    assert destination.joinpath(".attachments", str(repository.attachment_id), "original", "blob").read_bytes() == b"file"
    assert not source.exists()
    assert repository.released


def test_move_service_rejects_project_conversation_before_copy(tmp_path):
    service, repository, conversation_id, project_id, source, destination = _service(tmp_path)
    repository.conversation_project_id = str(uuid4())

    with pytest.raises(ConversationMoveError, match="普通会话"):
        asyncio.run(service.move_conversation_to_project(conversation_id, project_id, "user-1"))

    assert repository.move_calls == 0
    assert source.joinpath("notes.txt").is_file()
    assert not destination.joinpath("notes.txt").exists()
