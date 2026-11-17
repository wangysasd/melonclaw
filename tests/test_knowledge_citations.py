"""知识库回答必须带服务端生成的版本化可点击来源，不能只留 #source-* 锚点。"""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from melonclaw.api.sse import encode_event
from melonclaw.core.prompts import KNOWLEDGE_GUIDANCE, RESULT_OUTPUT_GUIDANCE, build_system_prompt
from melonclaw.output.assistant_steps import AssistantStepAccumulator, events_from_snapshot
from melonclaw.output.events import _consume_tool_call
from melonclaw.services.execution_trace import ExecutionTrace
from melonclaw.services.knowledge_search import (
    KnowledgeSearchService,
    citation_markdown,
    source_url,
)
from melonclaw.tool.knowledge import result as tool_result

VERSIONED_LINK = re.compile(r"\]\((/api/knowledge-bases/[^)\s]+/text\?[^)\s]+)\)")


def href(markdown: str) -> str:
    match = VERSIONED_LINK.search(markdown)
    assert match, f"不是可点击的版本化来源链接: {markdown}"
    return match.group(1)


def search_fixture():
    kb, doc, rev = (uuid4() for _ in range(3))
    rows = [dict(kb_id=kb, document_id=doc, revision_id=rev, chunk_id=uuid4(),
                 content="ERC-721 表示有唯一标识的独特资产", start_offset=1234,
                 end_offset=1256, title="以太坊智能合约标准 [v2]",
                 source_locator={"page": 7}, score=0.9)]
    repo = SimpleNamespace(
        knowledge_detail=AsyncMock(return_value={"knowledge_mode": "vector", "embedding_model_id": uuid4()}),
        fixed_embedding_model_id=AsyncMock(return_value=uuid4()),
        knowledge_search=AsyncMock(return_value=rows),
        knowledge_query_validate=AsyncMock(side_effect=lambda _user, _kb, raw: raw),
    )
    return KnowledgeSearchService(repo, SimpleNamespace(knowledge_embedding_dimensions=1024)), kb, doc, rev


def test_search_results_carry_server_generated_versioned_citations():
    service, kb, doc, rev = search_fixture()
    items = asyncio.run(service.search("alice", kb, {"query": "ERC", "mode": "keyword"}))["items"]
    assert len(items) == 1
    item = items[0]
    assert item["citation"] == citation_markdown(item["title"], item["source_url"])
    link = href(item["citation"])
    assert link == item["source_url"]
    parsed = urlparse(link)
    assert parsed.path == f"/api/knowledge-bases/{kb}/documents/{doc}/text"
    query = parse_qs(parsed.query)
    assert query["revision_id"] == [str(rev)]
    assert query["offset"] == ["1234"] and "limit" in query
    # 标题里的方括号被转义；模型复制后仍是单个完整链接，不会提前闭合。
    assert "\\[v2\\]" in item["citation"]
    assert item["citation"].count("](") == 1


def test_open_text_citation_uses_requested_revision_and_offset():
    service, kb, doc, rev = search_fixture()
    service.repository.knowledge_open_text = AsyncMock(return_value={
        "kb_id": kb, "document_id": doc, "revision_id": rev, "title": "稳定币条例 [扫描]",
        "content": "赎回条文", "offset": 250, "next_offset": None,
        "total_chars": 300, "is_current": True, "revision_no": 2,
    })
    value = asyncio.run(service.open_text("alice", kb, doc, revision_id=rev, offset=250, limit=1000))
    assert value["citation"] == citation_markdown(value["title"], value["source_url"])
    parsed = urlparse(href(value["citation"]))
    assert parsed.path == f"/api/knowledge-bases/{kb}/documents/{doc}/text"
    assert parse_qs(parsed.query)["revision_id"] == [str(rev)]
    assert parse_qs(parsed.query)["offset"] == ["250"]


def test_citation_never_invents_an_external_or_parameterless_url():
    url = source_url({"kb_id": uuid4(), "document_id": uuid4(), "revision_id": uuid4(), "start_offset": 7})
    assert url.startswith("/api/knowledge-bases/")
    assert parse_qs(urlparse(url).query)["offset"] == ["7"]
    for title in ["文档 [含] 方括号", "文档\\反斜杠", "多行\n标题"]:
        link = citation_markdown(title, url)
        assert href(link) == url
        assert link.count("](") == 1
        assert "\n" not in link


def test_system_prompt_forces_inline_citations_and_rejects_anchor_only_answers():
    prompt = build_system_prompt([])
    assert KNOWLEDGE_GUIDANCE in prompt and RESULT_OUTPUT_GUIDANCE in prompt
    assert "citation" in KNOWLEDGE_GUIDANCE and "source_url" in KNOWLEDGE_GUIDANCE
    assert "#source-*" in KNOWLEDGE_GUIDANCE and "以本段为准" in KNOWLEDGE_GUIDANCE
    assert "revision_id" in KNOWLEDGE_GUIDANCE and "offset" in KNOWLEDGE_GUIDANCE
    assert "知识库来源不适用本编号 sources 块" in RESULT_OUTPUT_GUIDANCE


def test_tool_notice_requires_citation_links_for_search_and_open():
    async def payload():
        return {"items": [{"content": "证据", "source_url": "/api/knowledge-bases/x/documents/y/text?revision_id=z"}]}

    cited = asyncio.run(tool_result(payload(), require_citation=True))
    assert "citation" in cited["notice"] and "#source-*" in cited["notice"]
    assert cited["untrusted_knowledge_data"]["items"][0]["source_url"]
    plain = asyncio.run(tool_result(payload()))
    assert "citation" not in plain["notice"]


def test_anchor_only_answers_are_not_clickable_sources():
    """复现 ERC 对照 40 锚点、稳定币赎回 16 锚点的失败形态。"""

    erc = "ERC-20 与 ERC-721 对照：\n" + "".join(f"论断 {i} [1](#source-1)\n" for i in range(40))
    erc += '```melon-result\n{"version":1,"type":"sources","items":[{"id":"1","title":"合约标准"}]}\n```'
    redemption = "赎回按面值处理。\n" + "".join(f"依据 {i} [1](#source-1)\n" for i in range(16))
    assert erc.count("#source-1") == 40 and redemption.count("#source-1") == 16
    assert VERSIONED_LINK.findall(erc) == []
    assert VERSIONED_LINK.findall(redemption) == []

    kb, doc, rev = (uuid4() for _ in range(3))
    link = source_url({"kb_id": kb, "document_id": doc, "revision_id": rev, "start_offset": 38772})
    fixed = f"ERC-721 表示独特资产 [以太坊智能合约标准]({link})；赎回按面值处理 [稳定币条例]({link})。"
    links = VERSIONED_LINK.findall(fixed)
    assert len(links) == 2 and all(value == link for value in links)
    parsed = urlparse(link)
    assert parsed.path == f"/api/knowledge-bases/{kb}/documents/{doc}/text"
    query = parse_qs(parsed.query)
    assert query["revision_id"] == [str(rev)] and query["offset"] == ["38772"]


@pytest.mark.parametrize('count', [6, 20])
@pytest.mark.parametrize('child', [False, True])
def test_full_sources_survive_tool_preview_sse_and_snapshot_replay(count, child):
    async def run():
        kb = uuid4()
        items = []
        for index in range(count):
            row = dict(kb_id=kb, document_id=uuid4(), revision_id=uuid4(), start_offset=index)
            items.append(dict(title=f'来源 {index}', source_url=source_url(row), content='资料正文' * 2000))

        async def payload():
            return {'items': items}

        result = await tool_result(payload())
        assert len(result['knowledge_sources']) == count

        async def deltas():
            if False:
                yield None

        call = SimpleNamespace(tool_call_id='call', tool_name='search_knowledge', input={}, error=None,
            output_deltas=deltas(), output=Command(update={'messages': [ToolMessage(
                content=json.dumps(result, ensure_ascii=False), tool_call_id='call')]}))
        accumulator = AssistantStepAccumulator(message_id='message', run_id='run')
        started = accumulator.start_step()
        queue = asyncio.Queue()
        scope = {'subagent_id': 'child', 'namespace': ('child',)} if child else None
        await _consume_tool_call(call, scope, queue, accumulator)
        emitted = []
        while not queue.empty():
            event = queue.get_nowait()
            emitted.append(json.loads(encode_event(event).decode().removeprefix('data: ').strip()))
        event = next(e for e in emitted if e['type'] == ('subagent_tool_result' if child else 'assistant_tool_result'))
        projected = event if child else event['result']
        assert [s['source_url'] for s in projected['knowledge_sources']] == [i['source_url'] for i in items]
        assert len(projected.get('content', projected.get('result_preview'))) < 4200
        if child:
            trace = ExecutionTrace(0)
            trace.remember(event)
            assert trace.metadata()['events'][0]['knowledge_sources'] == projected['knowledge_sources']
        else:
            consumer = AssistantStepAccumulator(message_id='message', run_id='run')
            consumer.apply_event(started)
            for item in emitted:
                consumer.apply_event(item)
            snapshot = consumer.terminal_snapshot(status='completed')
            reloaded = AssistantStepAccumulator(message_id='message', run_id='run', steps=snapshot)
            replay = next(e for e in events_from_snapshot('message', reloaded.steps) if e['type'] == 'assistant_tool_result')
            assert replay['result']['knowledge_sources'] == projected['knowledge_sources']

    asyncio.run(run())


