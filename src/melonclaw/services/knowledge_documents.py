"""上传与原件访问用例，外部文件名不参与存储路径。"""
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from melonclaw.core.knowledge import KnowledgeError, require_role
from melonclaw.core.knowledge_embedding import validate_embedding_settings
from melonclaw.services.knowledge_processing import processing_spec
from melonclaw.storage.knowledge import KnowledgeFiles

SUPPORTED = {".pdf": "application/pdf", ".doc": "application/msword",
             ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             ".ppt": "application/vnd.ms-powerpoint",
             ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
             ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
             ".tif": "image/tiff", ".tiff": "image/tiff",
             ".txt": "text/plain", ".md": "text/markdown"}


class KnowledgeDocuments:
    def __init__(self, settings, repository):
        self.settings, self.repository = settings, repository
        self.files = KnowledgeFiles(settings.data_root)

    async def upload(self, user_id, kb_id, upload, request_id, document_id=None):
        base = await self.repository.knowledge_detail(user_id, kb_id)
        require_role(base["role"], "documents")
        # 重放仍交给事务内幂等校验，避免活动请求的网络重试被误拦截。
        validate_embedding_settings(self.settings)
        name = (upload.filename or "").replace("\\", "/").split("/")[-1].strip()
        extension = Path(name).suffix.lower()
        if extension not in SUPPORTED or not name or len(name) > 255 or any(ord(c) < 32 for c in name):
            raise KnowledgeError("仅支持 PDF、Word、PowerPoint、PNG、JPEG、WebP、GIF、BMP、TIFF、TXT 和 Markdown，文件名须有效。")
        replace = document_id is not None
        document_id, revision_id = document_id or uuid4(), uuid4()
        source = self.files.source(kb_id, document_id, revision_id)
        source.parent.mkdir(parents=True, exist_ok=False)
        digest, count = hashlib.sha256(), 0
        keep = False
        try:
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while data := await upload.read(1024 * 1024):
                    count += len(data)
                    if count > self.settings.knowledge_max_file_bytes:
                        raise KnowledgeError("文件超过知识库大小限制。")
                    output.write(data)
                    digest.update(data)
            if not count:
                raise KnowledgeError("不能上传空文件。")
            result = await self.repository.knowledge_upload(
                user_id, kb_id, document_id=document_id, revision_id=revision_id, name=name,
                source_hash=digest.hexdigest(), source_byte_size=count, mime_type=SUPPORTED[extension],
                request_id=request_id, processing_spec={**processing_spec(extension), "source_name": name}, replace=replace,
            )
            keep = result["revision_id"] == revision_id
            return result
        finally:
            await upload.close()
            if not keep:
                source.unlink(missing_ok=True)
                source.parent.rmdir()

    async def download(self, user_id, kb_id, document_id):
        metadata = await self.repository.knowledge_source(user_id, kb_id, document_id)
        path = self.files.source(kb_id, document_id, metadata["id"])
        if not path.is_file():
            raise KnowledgeError("原件不可用，请联系开发人员检查存储。")
        metadata["display_name"] = metadata["processing_spec"]["source_name"]
        return path, metadata
