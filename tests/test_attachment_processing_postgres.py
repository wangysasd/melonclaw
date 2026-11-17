"""附件同步识别、重复上传和按消息绑定表示的集成验证。"""

import asyncio
import os
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import create_async_engine

from melonclaw.core.config import Settings
from melonclaw.core.knowledge_ocr import IMAGE_PURPOSE, OcrPageResult
from melonclaw.database.connection import normalize_async_database_url
from melonclaw.database.schema import metadata, tenants, users
from melonclaw.repository import AttachmentStateError, BusinessRepository
from melonclaw.repository.mappers import _now
from melonclaw.services.attachments import AttachmentService


class Upload:
    def __init__(self, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self.file = BytesIO(data)

    async def read(self, size=-1):
        return self.file.read(size)


@pytest.mark.skipif(not os.getenv("MELONCLAW_TEST_DATABASE_URL"), reason="需要隔离验证数据库连接")
def test_ocr_upload_reuses_claim_and_binds_visual_or_text_per_message(tmp_path, monkeypatch):
    async def run():
        schema = "test_attachment_processing_" + uuid4().hex
        engine = create_async_engine(
            normalize_async_database_url(os.environ["MELONCLAW_TEST_DATABASE_URL"]),
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.run_sync(metadata.create_all)
                await connection.execute(insert(tenants).values(
                    tenant_id="test", tenant_name_zh="测试", created_at=_now(),
                ))
                await connection.execute(insert(users).values(
                    user_id="owner", tenant_id="test", user_name_zh="用户", created_at=_now(),
                ))
            repo = BusinessRepository(SimpleNamespace(engine=engine))
            conversation_id = UUID((await repo.create_conversation("owner", None))["id"])

            async def model(_user_id, model_id):
                return SimpleNamespace(input_modalities={"text", "image"} if model_id == "custom:vision" else {"text"})

            settings = Settings(workspace_root=tmp_path / "workspace", deepseek_api_key="fixture")
            runtime = SimpleNamespace(
                require_ready=lambda: repo, settings=settings, storage=repo,
                resolve_model=model, conversation_workspace_dir=lambda cid: tmp_path / str(cid),
            )
            service = AttachmentService(runtime, SimpleNamespace(resolve_user=repo.get_user_context))
            await service.start()
            try:
                calls = []
                ocr_started, allow_ocr = asyncio.Event(), asyncio.Event()

                async def recognize(content, extension, **kwargs):
                    calls.append((extension, kwargs.get("purpose")))
                    if extension == ".png":
                        with Image.open(BytesIO(content)) as prepared:
                            assert prepared.format == "PNG"
                            assert prepared.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
                        ocr_started.set()
                        await allow_ocr.wait()
                        return [OcrPageResult(1, "识别结果", "识别结果", "stop")]
                    assert extension == ".pdf" and kwargs["purpose"] == IMAGE_PURPOSE
                    assert len(PdfReader(BytesIO(content)).pages) == 2
                    return [OcrPageResult(i, str(i), f"第 {i} 页正文", "stop") for i in [1, 2]]

                monkeypatch.setattr("melonclaw.services.attachment_ocr.recognize_file", recognize)
                red, blue = Image.new("RGB", (32, 32), "red"), Image.new("RGB", (32, 32), "blue")
                gif, tiff, single_page_tiff = BytesIO(), BytesIO(), BytesIO()
                red.save(gif, "GIF", save_all=True, append_images=[blue])
                red.save(tiff, "TIFF", save_all=True, append_images=[blue])
                red.save(single_page_tiff, "TIFF")
                uploaded_image = await service.upload(
                    None, "owner", Upload("image.tif", "image/tiff", single_page_tiff.getvalue()),
                    conversation_id=conversation_id, model_id="custom:vision",
                )
                image_id = UUID(uploaded_image["attachment_id"])
                preview, mime = await service.image_preview(image_id, "owner", conversation_id=conversation_id)
                assert mime == "image/png"
                with Image.open(BytesIO(preview)) as image:
                    assert image.getpixel((0, 0)) == (255, 0, 0)
                original, _ = await service.content_path(image_id, "owner", conversation_id=conversation_id)
                assert original.read_bytes() == single_page_tiff.getvalue()
                staged_tiff = await service.upload(
                    None, "owner", Upload("scan.tiff", "image/tiff", tiff.getvalue()),
                    conversation_id=conversation_id, model_id="custom:vision",
                )
                assert staged_tiff["kind"] == "document" and staged_tiff["parse_status"] == "processed"
                key = str(uuid4())

                async def upload_gif():
                    return await service.upload(
                        None, "owner", Upload("shot.gif", "image/gif", gif.getvalue()), key,
                        conversation_id=conversation_id, model_id="custom:vision",
                    )

                uploaded = await upload_gif()
                assert uploaded["first_frame_only"] and uploaded["parse_status"] == "not_required"
                attachment_id = UUID(uploaded["attachment_id"])

                async def pair(supports_image):
                    return await repo.create_message_pair(
                        conversation_id, "owner", None, str(uuid4()), "描述图片",
                        attachment_ids=[attachment_id], model_id="custom:chosen",
                        model_provider="test", model_name="test", model_display_name="测试模型",
                        model_supports_image=supports_image, max_attachment_count=10,
                        max_total_bytes=settings.attachment_max_total_bytes,
                    )

                with pytest.raises(AttachmentStateError) as rejected:
                    await pair(False)
                assert rejected.value.error_code == "attachment_ocr_required"
                visual = await pair(True)

                async def ocr():
                    return await service.retry_parse(
                        attachment_id, "owner", conversation_id=conversation_id, model_id="custom:text",
                    )

                attempt = asyncio.create_task(ocr())
                await asyncio.wait_for(ocr_started.wait(), timeout=5)
                try:
                    assert (await ocr())["parse_status"] == "processing"
                    assert (await upload_gif())["attachment_id"] == str(attachment_id)
                finally:
                    allow_ocr.set()
                    parsed = await attempt
                assert parsed["parse_status"] == "processed"
                assert (await ocr())["parse_status"] == "processed"
                assert calls == [(".pdf", IMAGE_PURPOSE), (".png", None)]
                textual = await pair(False)
                visual_block = (await service.hydration_provider(visual.user_message["id"]))["attachments"][0]
                text_block = (await service.hydration_provider(textual.user_message["id"]))["attachments"][0]
                assert visual_block["type"] == "image" and visual_block["mime_type"] == "image/png"
                assert text_block["type"] == "document" and text_block["path"].endswith("/derived/index.md")
                assert visual_block["first_frame_only"] and text_block["first_frame_only"]
            finally:
                await service.close()
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()

    asyncio.run(run())
