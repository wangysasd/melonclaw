"""附件上传、生命周期、解析调度和图片请求期 hydration。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from melonclaw.core.config import Settings
from melonclaw.core.knowledge_ocr import OCR_MAX_PAGES, SAFE_MESSAGES, OcrPageError
from melonclaw.parsers.documents import DocumentParseError
from melonclaw.parsers.validation import (
    SUPPORTED_TYPES,
    AttachmentValidationError,
    ValidatedAttachment,
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
from melonclaw.services.attachment_ocr import (
    ocr_file_limit,
    ocr_pdf_page_limit,
    parse_attachment_ocr,
    requires_document_ocr,
)
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
        interrupted = []
        for item in await storage.list_attachment_parse_queue():
            if item["kind"] == "image" or requires_document_ocr(item["original_name"], item["kind"]):
                interrupted.append(item["id"])
            else:
                self.schedule_parse(item["id"])
        if interrupted:
            await storage.interrupt_attachment_parses(interrupted)
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
        model_id: str | None = None,
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
        spec = SUPPORTED_TYPES.get(Path(upload.filename or "").suffix.lower())
        max_file_bytes = min(settings.attachment_max_file_bytes, settings.attachment_max_total_bytes)
        if spec and spec.ocr_required:
            max_file_bytes = ocr_file_limit(settings)
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
                    if size > max_file_bytes:
                        raise AttachmentError(
                            "单个附件大小超过限制。", "attachment_file_too_large", 413
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            attachment = await self._validate_source(candidate, upload.filename, upload.content_type)
            fingerprint = (digest.hexdigest(), size, attachment.kind, attachment.media_type)
            request_id = self._client_request_id(client_request_id)
            if request_id is not None:
                existing = await storage.find_attachment_upload(
                    context.user_id, project_id, conversation_id, request_id
                )
                if existing is not None:
                    self._compare_fingerprint(existing, fingerprint)
                    return self._attachment_response(existing, local)
            needs_ocr = requires_document_ocr(attachment.name, attachment.kind) or (
                attachment.kind == "image" and await self._is_text_model(context.user_id, model_id)
            )
            if needs_ocr and size > ocr_file_limit(settings):
                raise AttachmentError(SAFE_MESSAGES["ocr_file_too_large"], "ocr_file_too_large", 413)
            attachment_id = uuid4()
            try:
                local.publish_original(attachment_id, candidate, first_frame_only=attachment.first_frame_only)
                created = await storage.create_attachment(
                    attachment_id=attachment_id,
                    user_id=context.user_id,
                    project_id=project_id,
                    owner_conversation_id=conversation_id,
                    original_name=attachment.name,
                    media_type=attachment.media_type,
                    kind=attachment.kind,
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
                return self._attachment_response(existing, local)
            except Exception:
                local.remove_attachment(attachment_id)
                raise
            if needs_ocr:
                return await self._recognize(attachment_id, local, attachment)
            if created["parse_status"] == "pending":
                self.schedule_parse(attachment_id)
            return self._attachment_response(created, local)
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
        local = None
        if result["media_type"] in {"image/gif", "image/webp"}:
            workspace = await self._workspace_for_record({
                "project_id": project_id, "owner_conversation_id": conversation_id, "user_id": context.user_id,
            })
            local = LocalAttachmentStorage(workspace)
        return self._attachment_response(result, local)

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

    async def image_preview(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[bytes, str]:
        path, record = await self.content_path(
            attachment_id, user_id, project_id=project_id, conversation_id=conversation_id,
        )
        if record["kind"] != "image":
            raise AttachmentNotFoundError()
        try:
            encoded, media_type = await self._image_encoder_instance().encode(attachment_id, path, record["media_type"])
        except Exception:
            raise AttachmentError("无法预览图片。", "attachment_preview_failed", 422) from None
        return base64.b64decode(encoded), media_type

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

    async def _validate_source(
        self, source: Path, name: str | None, media_type: str | None,
    ) -> ValidatedAttachment:
        settings = self._settings()
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    validate_attachment, source, name, media_type,
                    image_max_pixels=settings.attachment_image_max_pixels,
                    pdf_max_pages=ocr_pdf_page_limit(settings),
                    ocr_max_pages=OCR_MAX_PAGES,
                    archive_max_entries=settings.attachment_archive_max_entries,
                    archive_max_uncompressed_bytes=settings.attachment_archive_max_uncompressed_bytes,
                    archive_max_entry_bytes=settings.attachment_archive_max_entry_bytes,
                    archive_max_compression_ratio=settings.attachment_archive_max_compression_ratio,
                ),
                timeout=settings.attachment_validation_timeout_seconds,
            )
        except TimeoutError as exc:
            raise AttachmentError("附件校验超时，请重试。", "attachment_validation_timeout", 422) from exc
        except AttachmentValidationError as exc:
            raise AttachmentError(str(exc), exc.error_code, exc.status_code) from exc

    async def _is_text_model(self, user_id: str, model_id: str | None) -> bool:
        if not model_id:
            return False
        model = await self.runtime.resolve_model(user_id, model_id)
        return "image" not in model.input_modalities

    async def _recognize(
        self, attachment_id: UUID, local: LocalAttachmentStorage,
        attachment: ValidatedAttachment | None = None,
    ) -> dict[str, Any]:
        await self._parse_one(attachment_id, on_request=True, attachment=attachment)
        return await self._current_attachment(attachment_id, local)

    async def _current_attachment(
        self, attachment_id: UUID, local: LocalAttachmentStorage,
    ) -> dict[str, Any]:
        result = await self._repository().get_attachment(attachment_id)
        if result is None or result["status"] in {"deleted", "expired"}:
            raise AttachmentNotFoundError()
        return self._attachment_response(result, local)

    async def _parse_one(
        self, attachment_id: UUID, *, on_request: bool = False,
        attachment: ValidatedAttachment | None = None,
    ) -> None:
        settings = self._settings()
        storage = self._repository()
        attempt_id = str(uuid4())
        record = await storage.claim_attachment_parse(
            attachment_id,
            attempt_id,
            lease_seconds=settings.attachment_parse_lease_seconds,
            max_attempts=settings.attachment_parse_max_attempts,
            on_request=on_request,
        )
        if record is None:
            return
        semaphore = self._parse_semaphore or asyncio.Semaphore(1)
        lease_task = asyncio.create_task(
            self._renew_parse_lease(attachment_id, attempt_id, settings.attachment_parse_lease_seconds)
        )
        temp_dir = None
        try:
            local = LocalAttachmentStorage(await self._workspace_for_record(record))
            source = local.original_path(attachment_id)
            temp_dir = local.create_parse_temp_dir(attachment_id)
            timeout = settings.attachment_parse_timeout_seconds if on_request else None
            async with asyncio.timeout(timeout), semaphore:
                if on_request:
                    if attachment is None:
                        attachment = await self._validate_source(
                            source, record["original_name"], record["media_type"],
                        )
                    await parse_attachment_ocr(settings, source, temp_dir, attachment)
                else:
                    await parse_document_isolated(
                        source,
                        temp_dir,
                        max_chars=settings.attachment_parse_max_chars,
                        timeout_seconds=settings.attachment_parse_timeout_seconds,
                        original_name=str(record["original_name"]),
                    )
                derived_size = sum(
                    item.stat().st_size for item in temp_dir.rglob("*") if item.is_file()
                )
                published = await storage.complete_attachment_parse(
                    attachment_id,
                    attempt_id,
                    project_id=record["project_id"],
                    owner_conversation_id=record["owner_conversation_id"],
                    derived_size_bytes=derived_size,
                    workspace_max_bytes=settings.attachment_project_max_bytes,
                    publish_derived=partial(local.publish_derived, attachment_id, temp_dir),
                )
                if not published:
                    await storage.fail_attachment_parse(attachment_id, attempt_id, "attachment_parse_interrupted")
        except asyncio.CancelledError:
            if on_request:
                await storage.fail_attachment_parse(attachment_id, attempt_id, "attachment_parse_interrupted")
            raise
        except OcrPageError as exc:
            await storage.fail_attachment_parse(attachment_id, attempt_id, exc.category)
        except (DocumentParseError, AttachmentError) as exc:
            await storage.fail_attachment_parse(attachment_id, attempt_id, exc.error_code)
        except (ParserTimeoutError, TimeoutError):
            await storage.fail_attachment_parse(attachment_id, attempt_id, "attachment_parse_timeout")
        except Exception:
            await storage.fail_attachment_parse(attachment_id, attempt_id, "attachment_parse_failed")
        finally:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)
            if temp_dir is not None and temp_dir.exists():
                local.remove_path(temp_dir)

    async def _renew_parse_lease(self, attachment_id: UUID, attempt_id: str, lease_seconds: int) -> None:
        interval = max(1, lease_seconds // 2)
        try:
            while True:
                await asyncio.sleep(interval)
                if not await self._repository().renew_attachment_parse_lease(
                    attachment_id,
                    attempt_id,
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
        first_frame_only = local.first_frame_only(record["id"])
        if record["kind"] != "image" or record.get("use_ocr"):
            if record["parse_status"] != "processed" or not (local.derived_dir(record["id"]) / "index.md").is_file():
                return self._unavailable_block(record)
            return {
                "type": "document",
                "attachment_id": str(record["id"]),
                "file_name": record["original_name"],
                "path": f"/.attachments/{record['id']}/derived/index.md",
                "first_frame_only": first_frame_only,
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
            "first_frame_only": first_frame_only,
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
                    "ocr": "always" if spec.ocr_required else "text_model" if spec.kind == "image" else None,
                }
                for spec in SUPPORTED_TYPES.values()
            ],
            "max_file_bytes": min(settings.attachment_max_file_bytes, settings.attachment_max_total_bytes),
            "ocr_max_file_bytes": ocr_file_limit(settings),
            "max_total_bytes": settings.attachment_max_total_bytes,
            "max_per_message": settings.attachment_max_per_message,
            "workspace_max_bytes": settings.attachment_project_max_bytes,
            "image_max_pixels": settings.attachment_image_max_pixels,
            "pdf_max_pages": ocr_pdf_page_limit(settings),
            "ocr_max_pages": OCR_MAX_PAGES,
        }

    async def retry_parse(
        self,
        attachment_id: UUID,
        user_id: str,
        *,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """同步识别 OCR 附件，或重置本地解析。"""

        context = await self.conversations.resolve_user(user_id)
        record = await self._scoped_record(
            attachment_id, context.user_id, project_id, conversation_id
        )
        local = LocalAttachmentStorage(await self._workspace_for_record(record))
        if requires_document_ocr(record["original_name"], record["kind"]):
            return await self._recognize(attachment_id, local)
        if record["kind"] == "image":
            if await self._is_text_model(context.user_id, model_id):
                return await self._recognize(attachment_id, local)
            return await self._current_attachment(attachment_id, local)
        result = await self._repository().reset_attachment_parse(
            attachment_id, context.user_id
        )
        if result is None:
            raise AttachmentNotFoundError()
        if result["parse_status"] == "pending":
            self.schedule_parse(attachment_id)
        return self._attachment_response(result, local)

    @staticmethod
    def _attachment_response(
        result: dict[str, Any], local: LocalAttachmentStorage | None,
    ) -> dict[str, Any]:
        response = {**result}
        if local is not None and result["media_type"] in {"image/webp", "image/gif"}:
            response["first_frame_only"] = local.first_frame_only(result["attachment_id"])
        code = result.get("parse_error_code")
        if code in SAFE_MESSAGES:
            response["parse_error_message"] = SAFE_MESSAGES[code]
        elif code == "attachment_parse_interrupted":
            response["parse_error_message"] = "附件识别已中断，请重新解析。"
        return response

    def _image_encoder_instance(self) -> OutboundImageEncoder:
        if self._image_encoder is None:
            settings = self._settings()
            self._image_encoder = OutboundImageEncoder(
                max_edge=settings.attachment_image_outbound_max_edge,
                jpeg_quality=settings.attachment_image_outbound_jpeg_quality,
                max_entries=settings.attachment_image_cache_entries,
            )
        return self._image_encoder

    def _settings(self) -> Settings:
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
