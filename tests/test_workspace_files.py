"""工作区浏览与旧交付读取共存的权限、文件系统和 HTTP 契约。"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from melonclaw.api.app import create_app
from melonclaw.services.conversations import ConversationService
from melonclaw.services.results import ResultFileService
from melonclaw.storage import workspace_files


def test_registered_attachment_query_filters_scope_status_expiry_and_literal_search():
    from contextlib import asynccontextmanager
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, insert

    from melonclaw.database.schema import chat_attachments
    from melonclaw.repository.attachments import AttachmentRepositoryMixin

    engine = create_engine("sqlite://")
    chat_attachments.create(engine)
    pid, other_pid, cid = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    base = {"user_id": "owner", "project_id": pid, "owner_conversation_id": None,
            "media_type": "text/plain", "kind": "text", "size_bytes": 4, "sha256": "fixture",
            "status": "attached", "parse_status": "processed", "created_at": now, "updated_at": now, "expires_at": None}
    variations = [
        {"original_name": "A.txt"}, {"original_name": "B%.txt", "updated_at": now + timedelta(seconds=1)},
        {"original_name": "other-user.txt", "user_id": "other"},
        {"original_name": "other-project.txt", "project_id": other_pid},
        {"original_name": "ordinary.txt", "project_id": None, "owner_conversation_id": cid},
        {"original_name": "deleted.txt", "status": "deleted"},
        {"original_name": "expired.txt", "status": "expired"},
        {"original_name": "staged-expired.txt", "status": "staged", "expires_at": now - timedelta(days=1)},
    ]
    with engine.begin() as connection:
        connection.execute(insert(chat_attachments), [{**base, **entry, "id": uuid4()} for entry in variations])

    @asynccontextmanager
    async def connect():
        with engine.connect() as connection:
            async def execute(statement):
                return connection.execute(statement)
            yield SimpleNamespace(execute=execute)

    repository = SimpleNamespace(engine=SimpleNamespace(connect=connect))
    async def run():
        query = AttachmentRepositoryMixin.list_workspace_attachments
        first = await query(repository, "owner", pid, None, "", "name", 0, 1)
        assert [item["file_name"] for item in first["items"]] == ["A.txt"]
        assert first["next_offset"] == 1
        second = await query(repository, "owner", pid, None, "", "name", 1, 1)
        assert [item["file_name"] for item in second["items"]] == ["B%.txt"]
        assert second["next_offset"] is None
        literal = await query(repository, "owner", pid, None, "%", "modified", 0, 200)
        assert [item["file_name"] for item in literal["items"]] == ["B%.txt"]
        ordinary = await query(repository, "owner", None, cid, "", "name", 0, 200)
        assert [item["file_name"] for item in ordinary["items"]] == ["ordinary.txt"]
    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_directory_is_bounded_paginated_and_skips_hidden_links_and_special_files(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "z.txt").write_text("Z")
    (tmp_path / "报告.txt").write_text("报告")
    (tmp_path / ".env").write_text("hidden fixture")
    (tmp_path / "alias").symlink_to(tmp_path / "z.txt")
    (tmp_path / "linked").symlink_to(tmp_path / "data", target_is_directory=True)
    os.mkfifo(tmp_path / "pipe")
    page = workspace_files.list_workspace_directory(tmp_path, "/", "", "name", 0, 1)
    assert page["items"][0]["name"] == "data"
    assert page["total"] == 3 and page["next_offset"] == 1
    next_page = workspace_files.list_workspace_directory(tmp_path, "/", "", "name", 1, 200)
    assert {item["name"] for item in next_page["items"]} == {"z.txt", "报告.txt"}
    assert next_page["next_offset"] is None
    assert workspace_files.list_workspace_directory(tmp_path, "/", "报告", "modified", 0, 200)["total"] == 1
    monkeypatch.setattr(workspace_files, "DIRECTORY_MAX_ENTRIES", 2)
    with pytest.raises(OverflowError):
        workspace_files.list_workspace_directory(tmp_path, "/", "", "name", 0, 200)


@pytest.mark.parametrize("path", ["relative", "/", "//data.txt", "/data/../private", "/.env", "/.attachments/file", "/data/.private/file", "/data/", "/data\\secret", "/data\x00", "/data\x7f"])
def test_workspace_reads_reject_invalid_paths(tmp_path, path):
    with pytest.raises(ValueError):
        workspace_files.open_workspace_file(tmp_path, path)


def test_workspace_open_rejects_links_and_keeps_an_open_descriptor(tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("original")
    (tmp_path / "alias.txt").symlink_to(path)
    (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    for invalid in ("/alias.txt", "/linked/data.txt"):
        with pytest.raises(OSError):
            workspace_files.open_workspace_file(tmp_path, invalid)
    with workspace_files.open_workspace_file(tmp_path, "/data.txt") as handle:
        path.unlink()
        path.symlink_to(tmp_path / "missing")
        assert handle.read() == b"original"


def test_real_file_routes_share_project_files_but_keep_scope_and_delivery_constraints(tmp_path):
    async def run():
        ordinary, project = tmp_path / "ordinary", tmp_path / "project"
        ordinary.mkdir()
        project.mkdir()
        (ordinary / "local.txt").write_text("local")
        (project / "src").mkdir()
        (project / "src/app.py").write_text("print('example')")
        html = project / "report.html"
        html.write_text("<button>current</button>")
        (project / ".artifacts").mkdir()
        (project / ".artifacts/internal.txt").write_text("internal fixture")
        pid, plain_id, first_id, second_id = uuid4(), uuid4(), uuid4(), uuid4()
        conversations = {str(cid): {"id": str(cid), "project_id": None if cid == plain_id else str(pid), "title": "测试对话"} for cid in (plain_id, first_id, second_id)}
        async def user_context(user):
            return SimpleNamespace(user_id=user) if user in {"owner", "other"} else None
        async def conversation(cid, user):
            return conversations.get(str(cid)) if user == "owner" else None
        attachment = {"attachment_id": str(uuid4()), "file_name": "原始资料.pdf", "size_bytes": 5, "modified_at": "2026-10-03T00:00:00Z"}
        storage = SimpleNamespace(get_user_context=AsyncMock(side_effect=user_context), get_conversation=AsyncMock(side_effect=conversation),
                                  get_project=AsyncMock(return_value={"id": str(pid), "name": "共享项目"}),
                                  list_workspace_attachments=AsyncMock(return_value={"items": [attachment], "next_offset": None}))
        runtime = SimpleNamespace(storage=storage, require_ready=lambda: storage,
                                  workspace_dir=Mock(side_effect=lambda _, proj: project if proj else ordinary),
                                  settings=SimpleNamespace(attachment_max_file_bytes=3_000_000, attachment_image_max_pixels=100))
        app = create_app()
        app.state.chat = SimpleNamespace(ready=True, results=ResultFileService(runtime, ConversationService(runtime)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for cid in (first_id, second_id):
                base = f"/api/conversations/{cid}/files"
                response = await client.get(base, params={"user_id": "owner"})
                assert response.status_code == 200
                assert response.json()["scope"] == {"kind": "project", "name": "共享项目"}
                assert {entry["name"] for entry in response.json()["items"]} == {"src", "report.html"}
                assert str(tmp_path) not in response.text
            base = f"/api/conversations/{first_id}/files"
            nested = await client.get(base, params={"user_id": "owner", "path": "/src", "q": "app", "limit": 1})
            assert nested.json()["items"][0]["path"] == "/src/app.py"
            for invalid in ({"limit": 201}, {"offset": -1}, {"sort": "unknown"}, {"q": "x" * 201}):
                assert (await client.get(base, params={"user_id": "owner", **invalid})).status_code == 422
            params = {"user_id": "owner", "path": "/report.html"}
            preview = await client.get(base + "/html-preview", params=params)
            assert preview.status_code == 200 and "sandbox allow-scripts" in preview.headers["content-security-policy"]
            assert "Content-Security-Policy" in preview.text and "current" in preview.text
            download = await client.get(base + "/content", params={**params, "preview": True})
            assert download.headers["content-disposition"].startswith("attachment;")
            assert download.text == html.read_text()
            html.write_text("<p>updated</p>")
            assert "updated" in (await client.get(base + "/html-preview", params=params)).text
            html.unlink()
            assert (await client.get(base + "/metadata", params=params)).status_code == 404
            old_delivery = await client.get(f"/api/conversations/{first_id}/result-files", params={"user_id": "owner", "path": "/src/app.py"})
            assert old_delivery.status_code == 404
            attachments = await client.get(base + "/attachments", params={"user_id": "owner"})
            assert attachments.json()["items"][0]["file_name"] == "原始资料.pdf"
            assert storage.list_workspace_attachments.await_args.args[:3] == ("owner", pid, None)
            plain = await client.get(f"/api/conversations/{plain_id}/files", params={"user_id": "owner"})
            assert [entry["name"] for entry in plain.json()["items"]] == ["local.txt"]
            for path in ("/.artifacts", "/src/..", "/../ordinary"):
                denied = await client.get(base, params={"user_id": "owner", "path": path})
                assert denied.status_code == 404 and str(tmp_path) not in denied.text
            for endpoint in ("", "/attachments", "/metadata", "/content", "/html-preview"):
                denied = await client.get(base + endpoint, params={"user_id": "other", "path": "/src/app.py"})
                assert denied.status_code == 404
            storage.get_project.return_value = None
            assert (await client.get(base, params={"user_id": "owner"})).status_code == 404
    asyncio.run(run())
