"""附件校验、解析和本地存储的无数据库单元测试。"""

from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from PIL import Image

from melonclaw.core.config import Settings
from melonclaw.parsers.documents import parse_document
from melonclaw.parsers.validation import AttachmentValidationError, validate_attachment
from melonclaw.parsers.worker import parse_document_isolated
from melonclaw.services import attachment_images
from melonclaw.services.attachment_images import OutboundImageEncoder
from melonclaw.services.attachments import AttachmentService
from melonclaw.storage import LocalAttachmentStorage


class AttachmentParserTests(unittest.TestCase):
    def test_validates_png_and_rejects_mismatched_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.png"
            Image.new("RGB", (2, 2), "#00aa00").save(image_path)

            self.assertEqual(
                validate_attachment(image_path, "image.png", "image/png"),
                ("image.png", "image/png", "image"),
            )
            with self.assertRaises(AttachmentValidationError) as context:
                validate_attachment(image_path, "image.jpg", "image/jpeg")
            self.assertEqual(context.exception.error_code, "magic_mismatch")

    def test_writes_markdown_parts_and_can_run_in_terminable_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "blob"
            source.write_text("第一行\n第二行", encoding="utf-8")
            output = root / "derived"

            parsed = parse_document(source, output, max_chars=100, original_name="notes.txt")
            self.assertEqual(parsed.character_count, 7)
            self.assertTrue((output / "index.md").is_file())
            self.assertTrue((output / "part-001.md").is_file())

            isolated_output = root / "isolated"
            asyncio.run(
                parse_document_isolated(
                    source,
                    isolated_output,
                    max_chars=100,
                    timeout_seconds=10,
                    original_name="renamed.txt",
                )
            )
            self.assertTrue((isolated_output / "index.md").is_file())

    def test_rejects_unsafe_file_name(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes.txt"
            source.write_text("safe", encoding="utf-8")
            with self.assertRaises(AttachmentValidationError) as context:
                validate_attachment(source, "../notes.txt", "text/plain")
            self.assertEqual(context.exception.error_code, "invalid_file_name")


class AttachmentStorageTests(unittest.TestCase):
    def test_original_and_derived_paths_are_attachment_id_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalAttachmentStorage(Path(directory))
            attachment_id = "2a7b1d6b-83cb-4b75-8cf4-ff3b0e3e1212"
            self.assertEqual(
                storage.original_path(attachment_id),
                (Path(directory) / ".attachments" / attachment_id / "original" / "blob").resolve(),
            )
            self.assertEqual(
                storage.derived_dir(attachment_id),
                (Path(directory) / ".attachments" / attachment_id / "derived").resolve(),
            )


class AttachmentValidationFrontLoadTests(unittest.TestCase):
    def test_rejects_invalid_json_structure_during_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.json"
            source.write_text("{not-json}", encoding="utf-8")
            with self.assertRaises(AttachmentValidationError) as context:
                validate_attachment(source, "data.json", "application/json")
            self.assertEqual(context.exception.error_code, "attachment_invalid")

    def test_accepts_valid_json_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "data.json"
            source.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(
                validate_attachment(source, "data.json", "application/json"),
                ("data.json", "application/json", "text"),
            )

    def test_rejects_non_utf8_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes.txt"
            source.write_bytes(b"\xff\xfe\x00bad")
            with self.assertRaises(AttachmentValidationError) as context:
                validate_attachment(source, "notes.txt", "text/plain")
            self.assertEqual(context.exception.error_code, "attachment_invalid")


class OutboundImageEncoderTests(unittest.TestCase):
    def test_downscales_oversized_image(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.png"
            Image.new("RGB", (400, 200), "#3366cc").save(source)
            encoder = OutboundImageEncoder(max_edge=100, jpeg_quality=80)

            encoded, media_type = asyncio.run(
                encoder.encode("att-1", source, "image/png")
            )

            self.assertEqual(media_type, "image/png")
            with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                self.assertEqual(max(image.size), 100)

    def test_caches_encoded_result_per_file_version(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.jpg"
            Image.new("RGB", (800, 600), "#221133").save(source)
            encoder = OutboundImageEncoder(max_edge=100, jpeg_quality=80)

            with mock.patch.object(
                attachment_images,
                "_encode_image",
                wraps=attachment_images._encode_image,
            ) as spy:
                first, _ = asyncio.run(encoder.encode("att-1", source, "image/jpeg"))
                second, _ = asyncio.run(encoder.encode("att-1", source, "image/jpeg"))

            self.assertEqual(first, second)
            self.assertEqual(spy.call_count, 1)


class _StubRuntime:
    """capabilities 只读取 settings，给 AttachmentService 一个最小运行时。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = None


class AttachmentCapabilitiesTests(unittest.TestCase):
    def test_capabilities_expose_supported_types_and_runtime_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(
                provider="deepseek",
                model_name="test-model",
                api_key="test-key",
                base_url=None,
                workspace_root=Path(directory),
                attachment_max_file_bytes=1234,
                attachment_max_per_message=3,
            )
            service = AttachmentService(_StubRuntime(settings), None)  # type: ignore[arg-type]

            capabilities = service.capabilities()

            extensions = {item["extension"] for item in capabilities["items"]}
            self.assertIn(".png", extensions)
            self.assertIn(".pdf", extensions)
            self.assertIn(".docx", extensions)
            self.assertEqual(capabilities["max_file_bytes"], 1234)
            self.assertEqual(capabilities["max_per_message"], 3)


if __name__ == "__main__":
    unittest.main()
