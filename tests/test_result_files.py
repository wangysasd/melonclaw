"""成果读取的归属、目录约束与内联内容边界。无需外部服务。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from melonclaw.api.app import create_app
from melonclaw.repository import AttachmentError, ConversationNotFoundError
from melonclaw.services.results import ResultFileService, _inspect
from melonclaw.storage.results import open_result_file


def test_paths_reject_hidden_traversal_symlinks_and_special_files(tmp_path):
    output = tmp_path / "outputs"
    output.mkdir()
    (output / "report.txt").write_text("结果", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    (output / "alias.txt").symlink_to(outside)
    (output / "linked").symlink_to(tmp_path, target_is_directory=True)
    for path in ["/private.txt", "/outputs/../private.txt", "/outputs/.env", "/outputs//report.txt", "/outputs/alias.txt", "/outputs/linked/private.txt", "/outputs/report.txt/", "/outputs/a\\b", "/outputs/."]:
        with pytest.raises((ValueError, OSError)):
            open_result_file(tmp_path, path)
    with open_result_file(tmp_path, "/outputs/report.txt") as handle:
        assert handle.read().decode() == "结果"


def test_inspection_previews_only_validated_image_pdf_and_small_text(tmp_path):
    (tmp_path / "outputs").mkdir()
    image = tmp_path / "outputs/image.png"
    Image.new("RGB", (2, 2)).save(image)
    with open_result_file(tmp_path, "/outputs/image.png") as handle:
        metadata = _inspect(handle, "/outputs/image.png", 1_000_000, 100)
        assert metadata["preview_kind"] == "image"
        assert handle.read(8) == b"\x89PNG\r\n\x1a\n"
    image.write_text("<script>alert(1)</script>")
    with open_result_file(tmp_path, "/outputs/image.png") as handle:
        with pytest.raises(AttachmentError):
            _inspect(handle, "/outputs/image.png", 1_000_000, 100)
    (tmp_path / "outputs/test.html").write_text("<script>alert(1)</script>")
    with open_result_file(tmp_path, "/outputs/test.html") as handle:
        assert _inspect(handle, "/outputs/test.html", 1_000_000, 100)["preview_kind"] is None
    with open_result_file(tmp_path, "/outputs/test.html") as handle:
        with pytest.raises(AttachmentError):
            _inspect(handle, "/outputs/test.html", 1, 100)


def test_service_resolves_user_and_checks_conversation_then_project(tmp_path):
    asyncio.run(_service_resolves_user_and_checks_conversation_then_project(tmp_path))


async def _service_resolves_user_and_checks_conversation_then_project(tmp_path):
    conversation_id = uuid4()
    repository = SimpleNamespace(get_conversation=AsyncMock(return_value=None))
    runtime = SimpleNamespace(require_ready=Mock(return_value=repository), workspace_dir=Mock(return_value=tmp_path), settings=SimpleNamespace(attachment_max_file_bytes=10000, attachment_image_max_pixels=100))
    conversations = SimpleNamespace(resolve_user=AsyncMock(return_value=SimpleNamespace(user_id="owner")), project_for_conversation=AsyncMock(return_value=None))
    service = ResultFileService(runtime, conversations)
    with pytest.raises(ConversationNotFoundError):
        await service.open(conversation_id, "other", "/outputs/file.txt")
    runtime.workspace_dir.assert_not_called()
    repository.get_conversation.assert_awaited_with(conversation_id, "owner")
    repository.get_conversation.return_value = {"id": str(conversation_id), "project_id": str(uuid4())}
    conversations.project_for_conversation.side_effect = ConversationNotFoundError
    with pytest.raises(ConversationNotFoundError):
        await service.open(conversation_id, "owner", "/outputs/file.txt")
    runtime.workspace_dir.assert_not_called()
    conversations.project_for_conversation.side_effect = None
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs/file.txt").write_text("hello")
    handle, metadata = await service.open(conversation_id, "owner", "/outputs/file.txt")
    with handle:
        assert handle.read() == b"hello"
    assert metadata["file_name"] == "file.txt"
    assert metadata["size_bytes"] == 5


def test_api_stream_has_safe_headers_and_download_default(tmp_path):
    asyncio.run(_api_stream_has_safe_headers_and_download_default(tmp_path))


async def _api_stream_has_safe_headers_and_download_default(tmp_path):
    app = create_app()
    path = tmp_path / "file.txt"
    path.write_text("<script>not executed</script>")
    async def open_file(*args):
        return path.open("rb"), {"file_name": "结果.txt", "media_type": "text/plain", "preview_kind": "text", "size_bytes": path.stat().st_size}
    app.state.chat = SimpleNamespace(ready=True, results=SimpleNamespace(open=open_file))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        route = f"/api/conversations/{uuid4()}/result-files"
        metadata = await client.get(route, params={"user_id": "owner", "path": "/outputs/file.txt"})
        assert metadata.status_code == 200 and metadata.json()["size_bytes"] == path.stat().st_size
        content = await client.get(route + "/content", params={"user_id": "owner", "path": "/outputs/file.txt"})
        assert content.status_code == 200 and content.text == path.read_text()
        assert content.headers["content-disposition"].startswith("attachment;")
        assert content.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in content.headers["content-security-policy"]
        assert content.headers["cache-control"] == "no-store"


def test_real_identity_and_project_service_reject_cross_scope(tmp_path):
    from melonclaw.repository import ProjectNotFoundError
    from melonclaw.services.conversations import ConversationService
    from melonclaw.services.errors import InvalidUserError

    async def run():
        cid, pid = uuid4(), uuid4()
        async def context(user):
            return SimpleNamespace(user_id=user) if user in {"owner", "other"} else None
        async def conversation(_cid, user):
            return {"id": str(cid), "project_id": str(pid)} if user == "owner" and _cid == cid else None
        repository = SimpleNamespace(get_user_context=AsyncMock(side_effect=context), get_conversation=AsyncMock(side_effect=conversation), get_project=AsyncMock(return_value=None))
        runtime = SimpleNamespace(storage=repository, require_ready=lambda: repository, workspace_dir=Mock(return_value=tmp_path))
        service = ResultFileService(runtime, ConversationService(runtime))
        for user, error in [("missing", InvalidUserError), ("other", ConversationNotFoundError), ("owner", ProjectNotFoundError)]:
            with pytest.raises(error):
                await service.open(cid, user, "/outputs/report.txt")
        repository.get_project.assert_awaited_with(pid, "owner")
        runtime.workspace_dir.assert_not_called()
    asyncio.run(run())
