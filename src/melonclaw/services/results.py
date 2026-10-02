"""会话成果的只读核验与下载；身份与工作区复用聊天服务。"""

from __future__ import annotations

import asyncio
import os
from pathlib import PurePosixPath
from typing import Any, BinaryIO
from uuid import UUID

from PIL import Image

from melonclaw.repository import AttachmentError, ConversationNotFoundError
from melonclaw.services.conversations import ConversationService
from melonclaw.services.runtime import ChatRuntime
from melonclaw.storage.results import open_result_file

_TEXT_TYPES = {
    ".txt": "text/plain", ".md": "text/plain", ".csv": "text/plain",
    ".json": "text/plain", ".py": "text/plain", ".js": "text/plain",
    ".ts": "text/plain", ".yaml": "text/plain", ".yml": "text/plain",
    ".diff": "text/plain", ".log": "text/plain",
}
_IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


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
    return {"file_name": name, "size_bytes": size, "media_type": media_type, "preview_kind": preview_kind}


class ResultFileService:
    def __init__(self, runtime: ChatRuntime, conversations: ConversationService) -> None:
        self.runtime = runtime
        self.conversations = conversations

    async def open(self, conversation_id: UUID, user_id: str, path: str) -> tuple[BinaryIO, dict[str, Any]]:
        storage = self.runtime.require_ready()
        context = await self.conversations.resolve_user(user_id)
        conversation = await storage.get_conversation(conversation_id, context.user_id)
        if conversation is None:
            raise ConversationNotFoundError
        project = await self.conversations.project_for_conversation(storage, conversation, context)
        workspace = self.runtime.workspace_dir(conversation, project)
        settings = self.runtime.settings
        if settings is None:
            raise RuntimeError("服务仍在启动。")
        handle = None
        try:
            handle = open_result_file(workspace, path)
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
