"""附件上传、生命周期、解析调度和图片请求期 hydration。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from melonclaw.parsers.documents import DocumentParseError
from melonclaw.parsers.validation import (
    SUPPORTED_TYPES,
    AttachmentValidationError,
    validate_attachment,
)
from melonclaw.parsers.worker import ParserTimeoutError, parse_document_isolated
from melonclaw.repository import (
    AttachmentConflictError,
    AttachmentError,
    AttachmentNotFoundError,
    BusinessRepository,
)
from melonclaw.services.attachment_images import OutboundImageEncoder
from melonclaw.services.conversations import ConversationService
from melonclaw.services.runtime import ChatRuntime
from melonclaw.storage import LocalAttachmentStorage


class AsyncUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes:
        ...


class AttachmentService:
    """附件用例门面；原始内容只在本地存储和模型出站请求间短暂存在。"""

    def __init__(self, runtime: ChatRuntime, conversations: ConversationService) -> None:
        self.runtime = runtime
        self.conversations = conversations
        self._tasks: set[asyncio.Task[None]] = set()
        self._parse_semaphore: asyncio.Semaphore | None = None
        self._image_encoder: OutboundImageEncoder | None = None

    async def start(self) -> None:
        settings = self.runtime.settings
        storage = self.runtime.storage
        if settings is None or storage is None:
            return
        self._parse_semaphore = asyncio.Semaphore(settings.attachment_parse_concurrency)
        for item in await storage.list_attachment_parse_queue():
            self.schedule_parse(UUID(item["attachment_id"]))
        try:
            await self.cleanup_expired()
        except Exception:
            # 清理失败不阻断正常启动；后台循环会在下一轮重试。
            pass
        cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._tasks.add(cleanup_task)
        cleanup_task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def upload(
        self,
        project_id: UUID | None,
        user_id: str,
        upload: AsyncUpload,
        client_request_id: str | None = None,
        *,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        settings = self._settings()
        storage = self._repository()
        context = await self.conversations.resolve_user(user_id)
        if (project_id is None) == (conversation_id is None):
            raise AttachmentNotFoundError()
        project = None
        conversation = None
        if project_id is not None:
            project = await storage.get_project(project_id, context.user_id)
            if project is None:
                raise AttachmentNotFoundError()
            workspace = self.runtime.project_workspace_dir(project)
        else:
            assert conversation_id is not None
            conversation = await storage.get_conversation(
                conversation_id, context.user_id
            )
            if conversation is None or conversation["project_id"] is not None:
                raise AttachmentNotFoundError()
            workspace = self.runtime.conversation_workspace_dir(conversation_id)
        local = LocalAttachmentStorage(workspace)
        descriptor, candidate = local.create_temp_file()
        size = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while True:
                    chunk = await upload.read(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > settings.attachment_max_file_bytes:
                        raise AttachmentError(
                            "单个附件大小超过限制。", "attachment_file_too_large", 413
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            try:
                name, media_type, kind = await asyncio.wait_for(
                    asyncio.to_thread(
                        validate_attachment,
                        candidate,
                        upload.filename,
                        upload.content_type,
                        image_max_pixels=settings.attachment_image_max_pixels,
                        pdf_max_pages=settings.attachment_pdf_max_pages,
                        archive_max_entries=settings.attachment_archive_max_entries,
                        archive_max_uncompressed_bytes=settings.attachment_archive_max_uncompressed_bytes,
                        archive_max_entry_bytes=settings.attachment_archive_max_entry_bytes,
                        archive_max_compression_ratio=settings.attachment_archive_max_compression_ratio,
                    ),
                    timeout=settings.attachment_validation_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise AttachmentError(
                    "附件校验超时，请重试。", "attachment_validation_timeout", 422
                ) from exc
            except AttachmentValidationError as exc:
                raise AttachmentError(str(exc), exc.error_code, exc.status_code) from exc
            fingerprint = (digest.hexdigest(), size, kind, media_type)
            request_id = self._client_request_id(client_request_id)
            if request_id is not None:
                existing = await storage.find_attachment_upload(
                    context.user_id, project_id, conversation_id, request_id
                )
                if existing is not None:
                    self._compare_fingerprint(existing, fingerprint)
                    return existing
            attachment_id = uuid4()
            local.publish_original(attachment_id, candidate)
            try:
                created = await storage.create_attachment(
                    attachment_id=attachment_id,
                    user_id=context.user_id,
                    project_id=project_id,
                    owner_conversation_id=conversation_id,
                    original_name=name,
                    media_type=media_type,
                    kind=kind,
                    size_bytes=size,
                    sha256=digest.hexdigest(),
                    client_request_id=request_id,
                    expires_at=datetime.now(UTC)
                    + timedelta(hours=settings.attachment_staged_ttl_hours),
                    workspace_max_bytes=settings.attachment_project_max_bytes,
                )
            except IntegrityError:
                local.remove_attachment(attachment_id)
                if request_id is None:
                    raise
                existing = await storage.find_attachment_upload(
                    context.user_id, project_id, conversation_id, request_id
                )
                if existing is None:
                    raise AttachmentError("附件登记失败。", "attachment_storage_error", 503)
                self._compare_fingerprint(existing, fingerprint)
                return existing
            except Exception:
                local.remove_attachment(attachment_id)
                raise
            if created["parse_status"] != "not_required":
                self.schedule_parse(UUID(created["attachment_id"]))
            return created
        finally:
            if candidate.exists():
                candidate.unlink()

    async def metadata(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        context = await self.conversations.resolve_user(user_id)
        self._require_scope(project_id, conversation_id)
        result = await self._repository().get_attachment_for_user(
            attachment_id,
            context.user_id,
            project_id=project_id,
            owner_conversation_id=conversation_id,
        )
        if result is None:
            raise AttachmentNotFoundError()
        return result

    async def content_path(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        context = await self.conversations.resolve_user(user_id)
        record = await self._scoped_record(
            attachment_id, context.user_id, project_id, conversation_id
        )
        if record is None:
            raise AttachmentNotFoundError()
        workspace = await self._workspace_for_record(record)
        path = LocalAttachmentStorage(workspace).original_path(attachment_id)
        if not path.is_file():
            raise AttachmentNotFoundError()
        return path, record

    async def delete(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        context = await self.conversations.resolve_user(user_id)
        storage = self._repository()
        record = await self._scoped_record(
            attachment_id, context.user_id, project_id, conversation_id
        )
        result = await storage.delete_staged_attachment(attachment_id, context.user_id)
        try:
            workspace = await self._workspace_for_record(record)
        except AttachmentNotFoundError:
            workspace = None
        if workspace is not None:
            try:
                LocalAttachmentStorage(workspace).remove_attachment(attachment_id)
                await storage.mark_storage_purged(attachment_id)
            except OSError:
                # 下次清理任务仍会看到 storage_purged_at=NULL，不提前释放配额。
                pass
        return result

    def schedule_parse(self, attachment_id: UUID) -> None:
        task = asyncio.create_task(self._parse_one(attachment_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def cleanup_expired(self) -> None:
        """标记并清理没有绑定消息的过期 staged 附件。"""

        storage = self._repository()
        for record in await storage.claim_expired_attachments():
            try:
                workspace = await self._workspace_for_record(record)
            except AttachmentNotFoundError:
                continue
            try:
                LocalAttachmentStorage(workspace).remove_attachment(record["id"])
                await storage.mark_storage_purged(UUID(str(record["id"])))
            except OSError:
                # 保留 storage_purged_at=NULL，下一轮继续尝试释放磁盘配额。
                continue

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30 * 60)
                try:
                    await self.cleanup_expired()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # 清理失败不应拖垮聊天服务；未标记 storage_purged_at 的记录
                    # 会在下一轮继续尝试，真实部署可在此接入指标/告警。
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            # 清理循环异常退出后由服务重启恢复；真实部署可在此接入指标/告警。
            return

    async def _parse_one(self, attachment_id: UUID) -> None:
        settings = self._settings()
        storage = self._repository()
        claim = await storage.claim_attachment_parse(
            attachment_id,
            self.runtime.worker_id,
            lease_seconds=settings.attachment_parse_lease_seconds,
            max_attempts=settings.attachment_parse_max_attempts,
        )
        if claim is None:
            return
        record = await storage.get_attachment_record(attachment_id)
        if record is None:
            return
        try:
            workspace = await self._workspace_for_record(record)
        except AttachmentNotFoundError:
            await storage.fail_attachment_parse(
                attachment_id, self.runtime.worker_id, "workspace_not_found"
            )
            return
        local = LocalAttachmentStorage(workspace)
        source = local.original_path(attachment_id)
        temp_dir = local.create_parse_temp_dir(attachment_id)
        semaphore = self._parse_semaphore or asyncio.Semaphore(1)
        lease_task = asyncio.create_task(
            self._renew_parse_lease(attachment_id, settings.attachment_parse_lease_seconds)
        )
        try:
            async with semaphore:
                try:
                    await parse_document_isolated(
                        source,
                        temp_dir,
                        max_chars=settings.attachment_parse_max_chars,
                        timeout_seconds=settings.attachment_parse_timeout_seconds,
                        original_name=str(record["original_name"]),
                    )
                except ParserTimeoutError:
                    await storage.fail_attachment_parse(
                        attachment_id, self.runtime.worker_id, "attachment_parse_timeout"
                    )
                    return
                except DocumentParseError as exc:
                    await storage.fail_attachment_parse(
                        attachment_id, self.runtime.worker_id, exc.error_code
                    )
                    return
                derived_size = sum(
                    item.stat().st_size for item in temp_dir.rglob("*") if item.is_file()
                )
                local.publish_derived(attachment_id, temp_dir)
                published = await storage.complete_attachment_parse(
                    attachment_id,
                    self.runtime.worker_id,
                    derived_size_bytes=derived_size,
                    workspace_max_bytes=settings.attachment_project_max_bytes,
                )
                if not published:
                    local.remove_path(local.derived_dir(attachment_id))
        except Exception:
            await storage.fail_attachment_parse(
                attachment_id, self.runtime.worker_id, "attachment_parse_failed"
            )
        finally:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)
            if temp_dir.exists():
                local.remove_path(temp_dir)

    async def _renew_parse_lease(self, attachment_id: UUID, lease_seconds: int) -> None:
        interval = max(1, lease_seconds // 2)
        try:
            while True:
                await asyncio.sleep(interval)
                if not await self._repository().renew_attachment_parse_lease(
                    attachment_id,
                    self.runtime.worker_id,
                    lease_seconds=lease_seconds,
                ):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # 续租瞬时失败时让当前解析完成或超时，最终 CAS 会拒绝过期 claim。
            return

    async def hydration_provider(self, user_message_id: str) -> dict[str, Any]:
        """按业务消息 ID 查附件，供请求期中间件构造模型内容块。

        历史重放、附件被清理或租户变化后的旧消息都可能引用已不可用的附件。
        这里对不可用附件降级为提示块，避免单个坏附件让整轮执行失败；真正的
        ``attachment_unavailable`` 错误只在发送前的绑定阶段抛出。
        """

        message_id = UUID(str(user_message_id))
        records = await self._repository().attachment_records_for_message(message_id)
        blocks: list[dict[str, Any]] = []
        for record in records:
            block = await self._hydration_block(record)
            if block is not None:
                blocks.append(block)
        return {"attachments": blocks}

    async def _hydration_block(self, record: dict[str, Any]) -> dict[str, Any]:
        if record["status"] in {"deleted", "expired"}:
            return self._unavailable_block(record)
        try:
            workspace = await self._workspace_for_record(record)
        except AttachmentNotFoundError:
            return self._unavailable_block(record)
        local = LocalAttachmentStorage(workspace)
        if record["kind"] == "archive":
            if not local.original_path(record["id"]).is_file():
                return self._unavailable_block(record)
            return {
                "type": "archive", "file_name": record["original_name"],
                "attachment_id": str(record["id"]),
            }
        if record["kind"] != "image":
            return {
                "type": "document",
                "attachment_id": str(record["id"]),
                "file_name": record["original_name"],
                "path": f"/.attachments/{record['id']}/derived/index.md",
            }
        path = local.original_path(record["id"])
        if not path.is_file():
            return self._unavailable_block(record)
        try:
            encoded, media_type = await self._image_encoder_instance().encode(
                record["id"], path, record["media_type"]
            )
        except Exception:
            # 图片解码失败同样降级，不让整轮执行带上不可恢复的附件错误。
            return self._unavailable_block(record)
        return {
            "type": "image",
            "attachment_id": str(record["id"]),
            "base64": encoded,
            "mime_type": media_type,
            "file_name": record["original_name"],
        }

    @staticmethod
    def _unavailable_block(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "unavailable",
            "file_name": record["original_name"],
        }

    def capabilities(self) -> dict[str, Any]:
        """返回前端可用于预校验的附件能力清单，作为类型与限制的单一来源。"""

        settings = self._settings()
        return {
            "items": [
                {
                    "extension": spec.extension,
                    "media_type": spec.media_type,
                    "kind": spec.kind,
                }
                for spec in SUPPORTED_TYPES.values()
            ],
            "max_file_bytes": settings.attachment_max_file_bytes,
            "max_total_bytes": settings.attachment_max_total_bytes,
            "max_per_message": settings.attachment_max_per_message,
            "workspace_max_bytes": settings.attachment_project_max_bytes,
            "image_max_pixels": settings.attachment_image_max_pixels,
            "pdf_max_pages": settings.attachment_pdf_max_pages,
        }

    async def retry_parse(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> dict[str, Any]:
        """重置可重试的失败解析并重新排队；已在解析中的直接返回当前状态。"""

        context = await self.conversations.resolve_user(user_id)
        await self._scoped_record(
            attachment_id, context.user_id, project_id, conversation_id
        )
        result = await self._repository().reset_attachment_parse(
            attachment_id, context.user_id
        )
        if result is None:
            raise AttachmentNotFoundError()
        if result["parse_status"] == "pending":
            self.schedule_parse(attachment_id)
        return result

    def _image_encoder_instance(self) -> OutboundImageEncoder:
        if self._image_encoder is None:
            settings = self._settings()
            self._image_encoder = OutboundImageEncoder(
                max_edge=settings.attachment_image_outbound_max_edge,
                jpeg_quality=settings.attachment_image_outbound_jpeg_quality,
                max_entries=settings.attachment_image_cache_entries,
            )
        return self._image_encoder

    def _settings(self):
        if self.runtime.settings is None:
            raise RuntimeError("运行配置尚未加载。")
        return self.runtime.settings

    def _repository(self) -> BusinessRepository:
        return self.runtime.require_ready()

    async def _workspace_for_record(self, record: dict[str, Any]) -> Path:
        storage = self._repository()
        if record["project_id"] is not None:
            project = await storage.get_project(
                UUID(str(record["project_id"])), str(record["user_id"])
            )
            if project is None:
                raise AttachmentNotFoundError()
            return self.runtime.project_workspace_dir(project)
        owner = record["owner_conversation_id"]
        if owner is None:
            raise AttachmentNotFoundError()
        conversation_id = UUID(str(owner))
        conversation = await storage.get_conversation(
            conversation_id, str(record["user_id"])
        )
        if conversation is None or conversation["project_id"] is not None:
            raise AttachmentNotFoundError()
        return self.runtime.conversation_workspace_dir(conversation_id)

    async def _scoped_record(
        self,
        attachment_id: UUID,
        user_id: str,
        project_id: UUID | None,
        conversation_id: UUID | None,
    ) -> dict[str, Any]:
        self._require_scope(project_id, conversation_id)
        record = await self._repository().get_attachment_record_for_user(
            attachment_id, user_id
        )
        if record is None:
            raise AttachmentNotFoundError()
        if project_id is not None and record["project_id"] != project_id:
            raise AttachmentNotFoundError()
        if (
            conversation_id is not None
            and record["owner_conversation_id"] != conversation_id
        ):
            raise AttachmentNotFoundError()
        return record

    @staticmethod
    def _require_scope(
        project_id: UUID | None, conversation_id: UUID | None
    ) -> None:
        if (project_id is None) == (conversation_id is None):
            raise AttachmentNotFoundError()

    @staticmethod
    def _client_request_id(value: str | None) -> str | None:
        if not value:
            return None
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise AttachmentError("client_request_id 必须是 UUID。", "invalid_client_request_id", 400) from exc
        if parsed.version != 4:
            raise AttachmentError("client_request_id 必须是 UUID v4。", "invalid_client_request_id", 400)
        return str(parsed)

    @staticmethod
    def _compare_fingerprint(existing: dict[str, Any], fingerprint: tuple[str, int, str, str]) -> None:
        if (
            existing["sha256"],
            existing["size_bytes"],
            existing["kind"],
            existing["media_type"],
        ) != fingerprint:
            raise AttachmentConflictError()
