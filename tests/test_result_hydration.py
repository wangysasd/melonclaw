"""附件真实 ID 只进入出站附件索引，不改写持久消息。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.messages import HumanMessage

from melonclaw.middleware.attachment_hydration import AttachmentHydrationMiddleware


def test_image_and_document_refs_have_real_ids_in_outbound_index():
    async def run():
        provider = AsyncMock(return_value={"attachments": [
            {"type": "image", "file_name": "plot.png", "attachment_id": "image-id", "mime_type": "image/png", "base64": "AA=="},
            {"type": "document", "file_name": "data.csv", "attachment_id": "document-id", "path": "/.attachments/document-id/derived/index.md"},
        ]})
        message = HumanMessage(content="分析这些附件", id="message-id")
        request = SimpleNamespace(messages=[message], override=lambda **kwargs: SimpleNamespace(**kwargs))
        handler = AsyncMock(return_value="ok")
        assert await AttachmentHydrationMiddleware(provider).awrap_model_call(request, handler) == "ok"
        sent = handler.call_args.args[0].messages[0]
        assert "attachment_id=image-id" in sent.content[0]["text"]
        assert "attachment_id=document-id" in sent.content[0]["text"]
        assert sent.content[1]["type"] == "image_url"
        assert message.content == "分析这些附件"
    asyncio.run(run())
