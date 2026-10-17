import asyncio

import httpx
import pytest
from pydantic import ValidationError

from melonclaw.core.task_policy import TaskExecutionError
from melonclaw.tool import deepseek as search_module
from melonclaw.tool.search import (
    DEEPSEEK_FETCH_URL,
    DEEPSEEK_SEARCH_URL,
    internet_search,
    validate_public_http_url,
    web_fetch,
)
from melonclaw.tool.task_search import TaskSearchInput, build_task_search_tool


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": "x" * 129},
        {"query": "x", "count": 11},
        {"query": "x", "count": 0},
        {"query": "x", "count": True},
        {"query": "x", "ori_question": " "},
        {"query": "x", "ori_question": "y" * 129},
        {"query": "x", "api_key": "injected"},
        {"query": "x", "api_base_url": "http://private"},
        {"query": "x", "need_content": True},
    ],
)
def test_search_rejects_unreviewed_inputs(payload):
    with pytest.raises(ValidationError):
        TaskSearchInput(**payload)


def test_real_task_search_tool_uses_fixed_bounded_search_and_discards_metadata(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "debug": "private-provider-metadata",
                "results": [
                    {
                        "title": "source",
                        "url": "https://example.org",
                        "summary": "x" * 9000,
                        "snippet": "short",
                        "content": "unbounded text",
                        "date": "2026-10-01T00:00:00+00:00",
                        "site_name": "Example",
                    },
                ],
            }

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            assert timeout == 30.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, *, headers, json):
            calls.append({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(search_module.httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        build_task_search_tool("fixture-key").ainvoke(
            {"query": "  公共资料  ", "ori_question": " 原始问题 "}
        )
    )
    assert calls[0]["url"] == DEEPSEEK_SEARCH_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer fixture-key"
    assert calls[0]["json"] == {
        "query": "公共资料",
        "count": 5,
        "need_content": False,
        "ori_question": "原始问题",
    }
    assert result["excerpts_truncated"] is True
    assert len(result["results"][0]["content"]) == 8000
    assert result["results"][0]["site_name"] == "Example"
    assert "private" not in repr(result) and "unbounded" not in repr(result)


def test_remote_exception_is_safe_and_cancellation_is_not_swallowed(monkeypatch):
    class FakeAsyncClient:
        mode = "error"

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            if self.mode == "cancel":
                raise asyncio.CancelledError
            raise RuntimeError("password=private-remote-error")

    monkeypatch.setattr(search_module.httpx, "AsyncClient", FakeAsyncClient)
    search = build_task_search_tool("fixture-key")
    with pytest.raises(TaskExecutionError) as caught:
        asyncio.run(search.ainvoke({"query": "x"}))
    assert "private" not in str(caught.value)
    assert caught.value.code == "search_failed"
    FakeAsyncClient.mode = "cancel"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(search.ainvoke({"query": "x"}))


def test_chat_search_and_fetch_tools_execute_and_redact_credentials(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *, timeout):
            assert timeout == 30.0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers, json):
            requests.append({"url": url, "headers": headers, "json": json})
            if url == DEEPSEEK_SEARCH_URL:
                return FakeResponse(
                    {
                        "results": [
                            {
                                "rerank_score": 2.4,
                                "title": "DeepSeek Search",
                                "url": "https://api-docs.deepseek.com/guides/search",
                                "snippet": "简短摘要",
                                "summary": "详细长摘要",
                                "content": "全文内容",
                                "site_name": "DeepSeek Docs",
                                "date": "2026-10-17T00:00:00+00:00",
                                "authority": 4,
                            }
                        ]
                    }
                )
            assert url == DEEPSEEK_FETCH_URL
            return FakeResponse(
                {
                    "title": "DeepSeek Fetch",
                    "content": "# Markdown\n\n" + ("正文" * 20000),
                }
            )

    monkeypatch.setattr(search_module.httpx, "Client", FakeClient)
    search_result = internet_search(
        "  DeepSeek site:deepseek.com  ",
        count=3,
        ori_question="  DeepSeek 搜索文档  ",
        need_content=False,
        api_key="sk-secret-deepseek-key",
    )
    assert requests[0]["json"] == {
        "query": "DeepSeek site:deepseek.com",
        "count": 3,
        "need_content": False,
        "ori_question": "DeepSeek 搜索文档",
    }
    assert search_result == {
        "results": [
            {
                "title": "DeepSeek Search",
                "url": "https://api-docs.deepseek.com/guides/search",
                "snippet": "简短摘要",
                "summary": "详细长摘要",
                "site_name": "DeepSeek Docs",
                "date": "2026-10-17T00:00:00+00:00",
                "rerank_score": 2.4,
                "authority": 4,
                "truncated_fields": [],
            }
        ]
    }

    fetch_result = web_fetch(
        " https://api-docs.deepseek.com/guides/search ",
        api_key="sk-secret-deepseek-key",
    )
    assert requests[1]["json"] == {"url": "https://api-docs.deepseek.com/guides/search"}
    assert fetch_result["title"] == "DeepSeek Fetch"
    assert fetch_result["content_truncated"] is True
    assert len(fetch_result["content"]) == 32_000


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "ftp://example.com/file",
        "http://user:pass@example.com/",
        "http://localhost:8000/api",
        "https://service.local/internal",
        "http://127.0.0.1:8080/health",
        "http://10.0.0.1/admin",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
    ],
)
def test_validate_public_http_url_rejects_unsafe_targets(bad_url):
    with pytest.raises(ValueError):
        validate_public_http_url(bad_url)


def test_deepseek_http_errors_never_echo_api_key(monkeypatch):
    class ErrorClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, headers, json):
            req = httpx.Request("POST", url, headers=headers)
            resp = httpx.Response(401, request=req, text="invalid key sk-secret-leak")
            raise httpx.HTTPStatusError("401 sk-secret-leak", request=req, response=resp)

    monkeypatch.setattr(search_module.httpx, "Client", ErrorClient)
    with pytest.raises(RuntimeError) as search_err:
        internet_search("test", api_key="sk-secret-leak")
    assert "sk-secret-leak" not in str(search_err.value)
    assert "DEEPSEEK_API_KEY" in str(search_err.value)

    with pytest.raises(RuntimeError) as fetch_err:
        web_fetch("https://example.com/doc", api_key="sk-secret-leak")
    assert "sk-secret-leak" not in str(fetch_err.value)
    assert "DEEPSEEK_API_KEY" in str(fetch_err.value)
