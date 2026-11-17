"""代理 DeepSeek 语音识别和朗读。不进入 Agent。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from typing import Any

import httpx
from anyio import CancelScope

ASR_URL = "https://api.deepseek.com/v1/tools/asr"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_DURATION_SECONDS = 120
CONNECT_TIMEOUT_SECONDS = 10.0
TOTAL_TIMEOUT_SECONDS = 180.0
SAMPLE_RATES = frozenset({8000, 16000, 22050, 24000, 32000, 44100, 48000})


class SpeechError(Exception):
    """可直接展示给用户的语音失败。消息不得包含凭据、上游原文或回复内容。"""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _UserGate:
    """单进程内每个用户同时只保留一个识别请求。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, user_id: str) -> None:
        with self._lock:
            if user_id in self._active:
                raise SpeechError("请求过多，请稍后再试。", 429)
            self._active.add(user_id)

    def release(self, user_id: str) -> None:
        with self._lock:
            self._active.discard(user_id)

    def clear(self) -> None:
        with self._lock:
            self._active.clear()


GATE = _UserGate()


def validate_wav(data: bytes) -> None:
    """接受文档允许的 16-bit PCM WAV。不合格一律 400，不访问网络。"""

    if len(data) > MAX_AUDIO_BYTES:
        raise SpeechError("音频文件过大。", 400)
    if len(data) < 44 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise SpeechError("音频无效。", 400)
    offset = 12
    audio_format = channels = sample_rate = bits = None
    data_size = 0
    saw_data = False
    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
        offset += 8
        if chunk_size < 0 or offset + chunk_size > len(data):
            raise SpeechError("音频无效。", 400)
        body = data[offset:offset + chunk_size]
        offset += chunk_size
        if chunk_size % 2 == 1 and offset < len(data):
            offset += 1
        if chunk_id == b"fmt ":
            if len(body) < 16:
                raise SpeechError("音频无效。", 400)
            audio_format = int.from_bytes(body[0:2], "little")
            channels = int.from_bytes(body[2:4], "little")
            sample_rate = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
        elif chunk_id == b"data":
            saw_data = True
            data_size += chunk_size
    if (
        audio_format != 1
        or channels not in (1, 2)
        or sample_rate not in SAMPLE_RATES
        or bits != 16
        or not saw_data
    ):
        raise SpeechError("音频无效。", 400)
    frame_bytes = channels * 2
    if data_size % frame_bytes != 0:
        raise SpeechError("音频无效。", 400)
    duration = (data_size // frame_bytes) / sample_rate
    if duration > MAX_DURATION_SECONDS:
        raise SpeechError("录音时长超过上限。", 400)


def _upstream_error(status_code: int) -> SpeechError:
    """把上游状态变成固定中文。401 不回传给浏览器，避免被当成登录失效。"""

    if status_code == 400:
        return SpeechError("音频无效。", 400)
    if status_code == 401:
        return SpeechError("语音识别鉴权失败。", 503)
    if status_code == 408:
        return SpeechError("上传超时，请稍后重试。", 408)
    if status_code == 413:
        return SpeechError("文件过大。", 413)
    if status_code == 429:
        return SpeechError("请求过多，请稍后再试。", 429)
    if status_code == 503:
        return SpeechError("语音识别服务繁忙，请稍后重试。", 503)
    if status_code == 504:
        return SpeechError("识别超时，请稍后重试。", 504)
    return SpeechError("语音识别失败，请稍后再试。", 503)


def _transcript(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SpeechError("语音识别失败，请稍后再试。", 503)
    text = payload.get("text")
    duration = payload.get("duration_ms")
    if not isinstance(text, str) or isinstance(duration, bool) or not isinstance(duration, int):
        raise SpeechError("语音识别失败，请稍后再试。", 503)
    if duration < 0:
        raise SpeechError("语音识别失败，请稍后再试。", 503)
    return {"text": text, "duration_ms": duration}


async def _post_audio(data: bytes, api_key: str) -> httpx.Response:
    timeout = httpx.Timeout(TOTAL_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(
            ASR_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("speech.wav", data, "audio/wav")},
        )


async def transcribe_audio(
    user_id: str,
    data: bytes,
    api_key: str,
    *,
    disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """校验 WAV 后整段上传。取消、断开和失败都会释放该用户的占用。"""

    key = api_key.strip()
    if not key:
        raise SpeechError("语音识别尚未配置。", 503)
    validate_wav(data)
    GATE.acquire(user_id)
    post_task: asyncio.Task[httpx.Response] | None = None
    try:
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
                post_task = asyncio.create_task(_post_audio(data, key))
                while not post_task.done():
                    if disconnected is not None and await disconnected():
                        raise SpeechError("语音识别已取消。", 499)
                    await asyncio.wait({post_task}, timeout=0.05)
                response = post_task.result()
        except (TimeoutError, httpx.TimeoutException):
            raise SpeechError("识别超时，请稍后重试。", 504) from None
        except httpx.HTTPError:
            raise SpeechError("语音识别失败，请稍后再试。", 503) from None
        if response.status_code != 200:
            raise _upstream_error(response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise SpeechError("语音识别失败，请稍后再试。", 503) from None
        return _transcript(payload)
    finally:
        try:
            if post_task is not None:
                if not post_task.done():
                    post_task.cancel()
                with CancelScope(shield=True):
                    await asyncio.gather(post_task, return_exceptions=True)
        finally:
            GATE.release(user_id)


TTS_URL = "https://api.deepseek.com/v1/tools/tts"
TTS_VOICES = frozenset({"stella", "tide"})
READABLE_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
MAX_BILLED_CHARS = 10_000
MAX_TTS_BODY_BYTES = 64 * 1024
TTS_DEADLINE_SECONDS = 31 * 60
TTS_GLOBAL_LIMIT = 8
_HAN_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B739),
    (0x2B740, 0x2B81D),
    (0x2B820, 0x2CEA1),
    (0x2CEB0, 0x2EBE0),
    (0x2EBF0, 0x2EE5D),
    (0x30000, 0x3134A),
    (0x31350, 0x323AF),
    (0x2F800, 0x2FA1D),
)


class _TtsGate:
    """单进程内每个用户同时一个合成，并限制全体并发。"""

    def __init__(self) -> None:
        self.limit = TTS_GLOBAL_LIMIT
        self._lock = threading.Lock()
        self._users: set[str] = set()

    def acquire(self, user_id: str) -> None:
        with self._lock:
            if user_id in self._users or len(self._users) >= self.limit:
                raise SpeechError("朗读请求过多，请稍后再试。", 429)
            self._users.add(user_id)

    def release(self, user_id: str) -> None:
        with self._lock:
            self._users.discard(user_id)

    def clear(self) -> None:
        with self._lock:
            self._users.clear()
            self.limit = TTS_GLOBAL_LIMIT


TTS_GATE = _TtsGate()


def _is_han(character: str) -> bool:
    code = ord(character)
    return any(start <= code <= end for start, end in _HAN_RANGES)


def billed_length(text: str) -> int:
    """汉字计 2，其余每个码位计 1。"""

    return sum(2 if _is_han(character) else 1 for character in text)


def normalize_voice(voice_id: str | None) -> str:
    if voice_id is None:
        return "stella"
    if voice_id not in TTS_VOICES:
        raise SpeechError("音色无效。", 400)
    return voice_id


def tts_request_body(text: str, voice: str) -> bytes:
    payload = {"text": text, "voice_id": voice, "format": "pcm", "filter_markdown": True}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def ensure_speakable(text: str, voice: str) -> bytes:
    payload = tts_request_body(text, voice)
    if billed_length(text) > MAX_BILLED_CHARS or len(payload) > MAX_TTS_BODY_BYTES:
        raise SpeechError("这段回复超过朗读长度上限。", 400)
    return payload


def prepare_speech_text(message: dict[str, Any] | None, voice: str) -> str:
    """只接受已结束助手消息的已保存正文。"""

    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise SpeechError("消息不存在。", 404)
    content = message.get("content")
    if message.get("status") not in READABLE_STATUSES:
        raise SpeechError("回复尚未结束，暂时不能朗读。", 400)
    if not isinstance(content, str) or not content.strip():
        raise SpeechError("没有可朗读的文本。", 400)
    ensure_speakable(content, voice)
    return content


def _tts_upstream_error(status_code: int) -> SpeechError:
    if status_code == 400:
        return SpeechError("这条回答暂时无法朗读", 400)
    if status_code == 401:
        return SpeechError("语音合成鉴权失败。", 503)
    if status_code == 429:
        return SpeechError("朗读请求过多，请稍后再试。", 429)
    if status_code == 503:
        return SpeechError("语音合成服务繁忙，请稍后重试。", 503)
    if status_code == 504:
        return SpeechError("朗读超时，请稍后重试。", 504)
    return SpeechError("朗读失败，请稍后再试。", 503)


async def _pcm_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_bytes():
        if chunk:
            yield chunk


@asynccontextmanager
async def open_tts(
    *,
    user_id: str,
    text: str,
    voice: str,
    api_key: str,
) -> AsyncIterator[AsyncIterator[bytes]]:
    """同一个作用域持有上游连接和并发名额，覆盖建连、读流及下游发送。"""

    key = api_key.strip()
    if not key:
        raise SpeechError("语音合成尚未配置。", 503)
    body = ensure_speakable(text, voice)
    TTS_GATE.acquire(user_id)
    client: httpx.AsyncClient | None = None
    response: httpx.Response | None = None
    try:
        async with asyncio.timeout(TTS_DEADLINE_SECONDS):
            client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=CONNECT_TIMEOUT_SECONDS))
            response = await client.send(
                client.build_request(
                    "POST",
                    TTS_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    content=body,
                ),
                stream=True,
            )
            if response.status_code != 200:
                raise _tts_upstream_error(response.status_code)
            async with aclosing(_pcm_chunks(response)) as audio:
                yield audio
    except (TimeoutError, httpx.TimeoutException):
        raise SpeechError("朗读超时，请稍后重试。", 504) from None
    except httpx.HTTPError:
        raise SpeechError("朗读失败，请稍后再试。", 503) from None
    finally:
        try:
            # ASGI 断开会取消整个发送作用域，关闭连接仍须完成。
            with CancelScope(shield=True):
                try:
                    if response is not None:
                        await response.aclose()
                finally:
                    if client is not None:
                        await client.aclose()
        finally:
            TTS_GATE.release(user_id)
