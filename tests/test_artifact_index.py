"""产物引用、完整历史索引与当前文件 HTML 预览的业务契约。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from melonclaw.api.app import create_app
from melonclaw.repository import ConversationNotFoundError
from melonclaw.services.result_index import extract_result_refs, message_result_refs
from melonclaw.services.results import ResultFileService


def block(value):
    return "```melon-result\n" + json.dumps(value) + "\n```"


def test_markdown_refs_are_explicit_deduplicated_and_ignore_code_sources_and_process():
    ref = {"path": "/outputs/报告.html"}
    attachment = {"attachment_id": str(uuid4())}
    content = "\n\n".join([
        "[查看](/outputs/%E6%8A%A5%E5%91%8A.html)",
        block({"version": 1, "type": "file", "ref": ref}),
        "![图](/outputs/image.png)",
        block({"version": 1, "type": "file", "ref": attachment}),
        "`[代码](/outputs/code.html)`",
        "```html\n[代码](/outputs/fenced.html)\n```",
        block({"version": 1, "type": "sources", "items": [{"id": "1", "title": "来源", "ref": {"path": "/outputs/source.html"}}]}),
        "[网页](https://example.com/outputs/web.html)",
        "[越界](/outputs/%2e%2e/private) [隐藏](/outputs/.env)",
        "[宿主](/etc/passwd)",
    ])
    assert extract_result_refs(content) == [ref, {"path": "/outputs/image.png"}, attachment]
    assert message_result_refs({"role": "assistant", "status": "pending", "content": content}) == []
    assert message_result_refs({"role": "user", "status": "completed", "content": content}) == []


@pytest.mark.parametrize("change", [
    {"version": True}, {"type": []}, {"ref": {"path": "/outputs/../secret"}},
    {"extra": "unsafe"}, {"caption": ""}, {"ref": {"url": "https://evil.test"}},
])
def test_invalid_file_result_never_becomes_a_delivery(change):
    assert extract_result_refs(block({"version": 1, "type": "file", "ref": {"path": "/outputs/x.html"}, **change})) == []


def test_unclosed_result_fence_and_indented_code_do_not_create_artifacts():
    assert extract_result_refs('```melon-result\n{"version":1,"type":"file","ref":{"path":"/outputs/x.html"}}') == []
    assert extract_result_refs("    [示例](/outputs/code.html)") == []
    assert extract_result_refs('[带空格](</outputs/a b.html>)') == [{"path": "/outputs/a b.html"}]


def test_nested_closed_result_fences_match_the_frontend_language_marker():
    value = {"version": 1, "type": "file", "ref": {"path": "/outputs/report.html"}}
    nested = "\n".join("> " + line for line in block(value).replace("melon-result", "melon-result json").splitlines())
    assert extract_result_refs(nested) == [value["ref"]]
    assert extract_result_refs(nested.rsplit("\n", 1)[0]) == []


def message(seq, path):
    return {"id": str(uuid4()), "role": "assistant", "status": "completed", "seq": seq,
            "content": f"[查看]({path})", "created_at": f"2026-10-03T00:{seq:02}:00Z"}


def test_index_reads_only_the_delivery_projection_and_checks_scope(monkeypatch):
    async def run():
        cid = uuid4()
        conversation = {"id": str(cid), "project_id": None}
        old, latest = message(1, "/outputs/old.html"), message(3, "/outputs/report.html")
        deliveries = [{"ref": {"path": "/outputs/report.html"}, "message_id": latest["id"], "created_at": latest["created_at"]},
                      {"ref": {"path": "/outputs/old.html"}, "message_id": old["id"], "created_at": old["created_at"]}]
        storage = SimpleNamespace(get_conversation=AsyncMock(return_value=conversation),
                                  list_artifacts=AsyncMock(return_value=deliveries),
                                  list_messages=AsyncMock(side_effect=AssertionError("不得读取消息历史")))
        def fail_parse(_content):
            raise AssertionError("不得在产物读取时解析 Markdown")
        monkeypatch.setattr("melonclaw.services.result_index.extract_result_refs", fail_parse)
        conversations = SimpleNamespace(resolve_user=AsyncMock(return_value=SimpleNamespace(user_id="owner")),
                                        project_for_conversation=AsyncMock(return_value=None))
        service = ResultFileService(SimpleNamespace(require_ready=lambda: storage), conversations)
        items = await service.index(cid, "owner")
        assert [item["ref"]["path"] for item in items] == ["/outputs/report.html", "/outputs/old.html"]
        assert items[0]["message_id"] == latest["id"]
        assert items == deliveries
        await service.index(cid, "owner")
        assert storage.list_artifacts.await_count == 2
        storage.list_artifacts.assert_awaited_with(cid, "owner")
        storage.list_messages.assert_not_awaited()
        storage.get_conversation.return_value = None
        with pytest.raises(ConversationNotFoundError):
            await service.index(cid, "other")
        assert storage.list_artifacts.await_count == 2
    asyncio.run(run())


def test_real_html_route_uses_current_file_and_keeps_download_restricted(tmp_path):
    async def run():
        cid = uuid4()
        output = tmp_path / "outputs"
        output.mkdir()
        html = output / "report.html"
        html.write_text('<html><body><button onclick="this.textContent=\'OK\'">Click</button></body></html>', encoding="utf-8")
        repository = SimpleNamespace(get_conversation=AsyncMock(return_value={"id": str(cid), "project_id": None}))
        runtime = SimpleNamespace(require_ready=lambda: repository, workspace_dir=lambda *_: tmp_path,
                                  settings=SimpleNamespace(attachment_max_file_bytes=3_000_000, attachment_image_max_pixels=100))
        conversations = SimpleNamespace(resolve_user=AsyncMock(return_value=SimpleNamespace(user_id="owner")),
                                        project_for_conversation=AsyncMock(return_value=None))
        app = create_app()
        app.state.chat = SimpleNamespace(ready=True, results=ResultFileService(runtime, conversations))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            route = f"/api/conversations/{cid}/result-files"
            params = {"user_id": "owner", "path": "/outputs/report.html"}
            preview = await client.get(route + "/html-preview", params=params)
            assert preview.status_code == 200
            policy = preview.headers["content-security-policy"]
            assert "sandbox allow-scripts" in policy and "allow-same-origin" not in policy
            assert "connect-src 'none'" in policy and "img-src data:" in policy
            assert preview.text.index("Content-Security-Policy") < preview.text.index("<button")
            assert preview.headers["cache-control"] == "no-store"
            assert preview.headers["x-content-type-options"] == "nosniff"
            download = await client.get(route + "/content", params={**params, "preview": "true"})
            assert download.headers["content-disposition"].startswith("attachment;")
            assert download.text == html.read_text()
            html.write_text("<p>updated</p>", encoding="utf-8")
            updated = await client.get(route + "/html-preview", params=params)
            assert "updated" in updated.text and "<button" not in updated.text
            html.write_bytes(b"\xff\xfe")
            invalid = await client.get(route + "/html-preview", params=params)
            assert invalid.status_code == 422
            html.write_bytes(b"x" * 2_000_001)
            large = await client.get(route + "/html-preview", params=params)
            assert large.status_code == 422
            (output / "plain.txt").write_text("text")
            plain = await client.get(route + "/html-preview", params={**params, "path": "/outputs/plain.txt"})
            assert plain.status_code == 422
            repository.get_conversation.return_value = None
            denied = await client.get(route + "/html-preview", params=params)
            assert denied.status_code == 404
    asyncio.run(run())
