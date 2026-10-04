"""会话成果的只读核验与下载；身份与工作区复用聊天服务。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

from PIL import Image

from melonclaw.repository import AttachmentError, ConversationNotFoundError
from melonclaw.services.conversations import ConversationService
from melonclaw.services.runtime import ChatRuntime
from melonclaw.storage.results import open_result_file
from melonclaw.storage.workspace_files import list_workspace_directory, open_workspace_file

_TEXT_TYPES = {
    ".txt": "text/plain", ".md": "text/plain", ".csv": "text/plain",
    ".json": "text/plain", ".py": "text/plain", ".js": "text/plain",
    ".ts": "text/plain", ".yaml": "text/plain", ".yml": "text/plain",
    ".diff": "text/plain", ".log": "text/plain", ".css": "text/plain",
    ".tsx": "text/plain", ".jsx": "text/plain", ".toml": "text/plain",
    ".xml": "text/plain", ".sh": "text/plain", ".sql": "text/plain",
}
_IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
HTML_PREVIEW_MAX_BYTES = 2_000_000


def _inspect(handle: BinaryIO, path: str, max_bytes: int, max_pixels: int) -> dict[str, Any]:
    size = os.fstat(handle.fileno()).st_size
    if size > max_bytes:
        raise AttachmentError("成果文件超过下载大小限制。", "result_too_large", 413)
    name = PurePosixPath(path).name
    extension = PurePosixPath(name).suffix.lower()
    media_type, preview_kind = "application/octet-stream", None
    if extension in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        try:
            with Image.open(handle) as image:
                if image.width * image.height > max_pixels or image.format not in _IMAGE_TYPES:
                    raise ValueError("图片超出预览范围。")
                media_type = _IMAGE_TYPES[image.format]
                image.verify()
            preview_kind = "image"
        except Exception as exc:
            raise AttachmentError("成果图片无效，无法预览。", "result_image_invalid", 422) from exc
        finally:
            handle.seek(0)
    elif extension == ".pdf":
        if handle.read(5) == b"%PDF-":
            media_type, preview_kind = "application/pdf", "pdf"
        handle.seek(0)
    elif extension in _TEXT_TYPES and size <= 200_000:
        try:
            handle.read().decode("utf-8")
            media_type, preview_kind = _TEXT_TYPES[extension], "text"
        except UnicodeDecodeError:
            pass
        finally:
            handle.seek(0)
    elif extension in {".html", ".htm"}:
        media_type = "text/html"
        if size <= HTML_PREVIEW_MAX_BYTES:
            try:
                handle.read(HTML_PREVIEW_MAX_BYTES + 1).decode("utf-8")
                preview_kind = "html"
            except UnicodeDecodeError:
                pass
            finally:
                handle.seek(0)
    return {"file_name": name, "size_bytes": size, "media_type": media_type, "preview_kind": preview_kind}


class ResultFileService:
    def __init__(self, runtime: ChatRuntime, conversations: ConversationService) -> None:
        self.runtime = runtime
        self.conversations = conversations

    async def index(self, conversation_id: UUID, user_id: str) -> list[dict[str, Any]]:
        """校验会话归属后读取交付投影，不加载或解析消息历史。"""
        storage = self.runtime.require_ready()
        context = await self.conversations.resolve_user(user_id)
        conversation = await storage.get_conversation(conversation_id, context.user_id)
        if conversation is None:
            raise ConversationNotFoundError
        await self.conversations.project_for_conversation(storage, conversation, context)
        return await storage.list_artifacts(conversation_id, context.user_id)

    async def open(self, conversation_id: UUID, user_id: str, path: str) -> tuple[BinaryIO, dict[str, Any]]:
        return await self._open(conversation_id, user_id, path, workspace_file=False)

    async def open_workspace(self, conversation_id: UUID, user_id: str, path: str) -> tuple[BinaryIO, dict[str, Any]]:
        return await self._open(conversation_id, user_id, path, workspace_file=True)

    async def _workspace(self, conversation_id: UUID, user_id: str) -> tuple[Path, dict[str, Any], bool]:
        storage = self.runtime.require_ready()
        context = await self.conversations.resolve_user(user_id)
        conversation = await storage.get_conversation(conversation_id, context.user_id)
        if conversation is None:
            raise ConversationNotFoundError
        project = await self.conversations.project_for_conversation(storage, conversation, context)
        workspace = self.runtime.workspace_dir(conversation, project)
        return workspace, project if project is not None else conversation, project is not None

    async def directory(self, conversation_id: UUID, user_id: str, path: str, query: str, sort: str, offset: int, limit: int) -> dict[str, Any]:
        workspace, owner, project = await self._workspace(conversation_id, user_id)
        scope = {"kind": "project" if project else "conversation", "name": owner["name"] if project else owner["title"]}
        try:
            directory = await asyncio.to_thread(list_workspace_directory, workspace, path, query, sort, offset, limit)
        except OverflowError as exc:
            raise AttachmentError(str(exc), "directory_too_large", 413) from exc
        except (OSError, ValueError) as exc:
            raise AttachmentError("目录不存在或不可访问。", "directory_unavailable", 404) from exc
        return {**directory, "scope": scope}

    async def attachments(self, conversation_id: UUID, user_id: str, query: str, sort: str, offset: int, limit: int) -> dict[str, Any]:
        _, owner, project = await self._workspace(conversation_id, user_id)
        context = await self.conversations.resolve_user(user_id)
        owner_id = UUID(str(owner["id"]))
        return await self.runtime.require_ready().list_workspace_attachments(
            context.user_id, owner_id if project else None, None if project else owner_id,
            query, sort, offset, limit,
        )

    async def _open(self, conversation_id: UUID, user_id: str, path: str, *, workspace_file: bool) -> tuple[BinaryIO, dict[str, Any]]:
        workspace, _, _ = await self._workspace(conversation_id, user_id)
        settings = self.runtime.settings
        if settings is None:
            raise RuntimeError("服务仍在启动。")
        handle = None
        try:
            handle = open_workspace_file(workspace, path) if workspace_file else open_result_file(workspace, path)
            metadata = await asyncio.to_thread(
                _inspect, handle, path, settings.attachment_max_file_bytes, settings.attachment_image_max_pixels,
            )
            return handle, metadata
        except AttachmentError:
            if handle is not None:
                handle.close()
            raise
        except (OSError, ValueError) as exc:
            if handle is not None:
                handle.close()
            raise AttachmentError("成果文件不存在或不可访问。", "result_unavailable", 404) from exc
