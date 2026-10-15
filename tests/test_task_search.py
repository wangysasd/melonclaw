import asyncio

import pytest
from pydantic import ValidationError

from melonclaw.core.task_policy import TaskExecutionError
from melonclaw.tool import task_search
from melonclaw.tool.task_search import TaskSearchInput, build_task_search_tool


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": "x" * 401},
        {"query": "x", "max_results": 11},
        {"query": "x", "max_results": 0},
        {"query": "x", "max_results": True},
        {"query": "x", "topic": "unsafe"},
        {"query": "x", "api_key": "injected"},
        {"query": "x", "api_base_url": "http://private"},
        {"query": "x", "include_raw_content": True},
    ],
)
def test_search_rejects_unreviewed_inputs(payload):
    with pytest.raises(ValidationError):
        TaskSearchInput(**payload)


def test_real_tool_uses_fixed_bounded_search_and_discards_metadata(monkeypatch):
    calls = []

    class Client:
        def __init__(self, *, api_key, client):
            assert api_key == "fixture-key"

        async def search(self, **kwargs):
            calls.append(kwargs)
            return {
                "debug": "private-provider-metadata",
                "results": [
                    {
                        "title": "source",
                        "url": "https://example.org",
                        "content": "x" * 9000,
                        "raw_content": "unbounded text",
                    },
                ],
            }

    monkeypatch.setattr(task_search, "AsyncTavilyClient", Client)
    result = asyncio.run(build_task_search_tool("fixture-key").ainvoke({"query": "  公共资料  "}))
    assert calls[0]["query"] == "公共资料"
    assert calls[0]["include_raw_content"] is False and calls[0]["include_images"] is False
    assert calls[0]["timeout"] == 30
    assert result["excerpts_truncated"] is True
    assert len(result["results"][0]["content"]) == 8000
    assert "private" not in repr(result) and "raw_content" not in repr(result)


def test_remote_exception_is_safe_and_cancellation_is_not_swallowed(monkeypatch):
    class Client:
        mode = "error"

        def __init__(self, **kwargs):
            pass

        async def search(self, **kwargs):
            if self.mode == "cancel":
                raise asyncio.CancelledError
            raise RuntimeError("password=private-remote-error")

    monkeypatch.setattr(task_search, "AsyncTavilyClient", Client)
    search = build_task_search_tool("fixture-key")
    with pytest.raises(TaskExecutionError) as caught:
        asyncio.run(search.ainvoke({"query": "x"}))
    assert "private" not in str(caught.value)
    assert caught.value.code == "search_failed"
    Client.mode = "cancel"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(search.ainvoke({"query": "x"}))
