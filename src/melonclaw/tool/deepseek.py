"""DeepSeek 只读工具共用的协议、有限重试和进程内请求节流。"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
DEEPSEEK_FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"
DEEPSEEK_TOOL_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3
REQUEST_INTERVAL_SECONDS = 0.21
MAX_RETRY_DELAY_SECONDS = 10.0


class DeepSeekResponseError(RuntimeError):
    """不携带上游原文或 Pydantic 输入的协议错误。"""


class SearchResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)
    title: str
    url: str = Field(min_length=1, max_length=8192)
    summary: str
    snippet: str = ""
    content: str = ""
    site_name: str | None = None
    date: str | None = None
    rerank_score: float | None = None
    authority: int | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    results: list[SearchResult]


class FetchResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    title: str
    content: str


def validate_response(data: Any, *, fetch: bool = False) -> dict[str, Any]:
    try:
        parsed = (FetchResponse if fetch else SearchResponse).model_validate(data)
        if fetch and not parsed.content.strip():
            raise ValueError("empty content")
    except (ValidationError, ValueError):
        raise DeepSeekResponseError("DeepSeek 接口响应格式异常，请稍后重试。") from None
    return parsed.model_dump()


class RequestPacer:
    """Search/Fetch 各自共享一个节流器；锁仅保护时间，不跨等待或网络请求。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def delay(self, endpoint: str) -> float:
        with self._lock:
            now = time.monotonic()
            delay = self._next.get(endpoint, 0.0) - now
            if delay <= 0:
                self._next[endpoint] = now + REQUEST_INTERVAL_SECONDS
                return 0.0
            return delay

    def wait(self, endpoint: str) -> None:
        while delay := self.delay(endpoint):
            time.sleep(delay)

    async def await_slot(self, endpoint: str) -> None:
        while delay := self.delay(endpoint):
            await asyncio.sleep(delay)


_PACER = RequestPacer()


def retry_delay(exc: httpx.HTTPError, attempt: int) -> float | None:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status not in {408, 429} and not 500 <= status < 600:
            return None
        raw = exc.response.headers.get("Retry-After")
        if raw is not None:
            try:
                seconds = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        return None
                    seconds = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    return None
            # 不提前重试长时间的限流，也不接受 NaN/Infinity。
            if seconds > MAX_RETRY_DELAY_SECONDS:
                return None
            if 0 <= seconds <= MAX_RETRY_DELAY_SECONDS:
                return max(seconds, 0.5 * 2**attempt)
            return None
    elif not isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return None
    return 0.5 * 2**attempt


def request_json(endpoint: str, payload: dict[str, Any], api_key: str) -> Any:
    with httpx.Client(timeout=DEEPSEEK_TOOL_TIMEOUT_SECONDS) as client:
        for attempt in range(MAX_ATTEMPTS):
            _PACER.wait(endpoint)
            try:
                response = client.post(endpoint, headers={
                    "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                }, json=payload)
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    raise DeepSeekResponseError("DeepSeek 接口响应格式异常，请稍后重试。") from None
            except httpx.HTTPError as exc:
                delay = retry_delay(exc, attempt)
                if attempt == MAX_ATTEMPTS - 1 or delay is None:
                    raise
                time.sleep(delay)


async def request_json_async(endpoint: str, payload: dict[str, Any], api_key: str) -> Any:
    async with httpx.AsyncClient(timeout=DEEPSEEK_TOOL_TIMEOUT_SECONDS) as client:
        for attempt in range(MAX_ATTEMPTS):
            await _PACER.await_slot(endpoint)
            try:
                response = await client.post(endpoint, headers={
                    "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                }, json=payload)
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    raise DeepSeekResponseError("DeepSeek 接口响应格式异常，请稍后重试。") from None
            except httpx.HTTPError as exc:
                delay = retry_delay(exc, attempt)
                if attempt == MAX_ATTEMPTS - 1 or delay is None:
                    raise
                await asyncio.sleep(delay)
