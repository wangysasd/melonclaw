"""语音输入与朗读的 HTTP 边界。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from anyio import create_task_group
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict
from python_multipart.exceptions import MultipartParseError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from melonclaw.api.auth import account_session, assert_request_user, check_origin
from melonclaw.api.dependencies import get_chat_service
from melonclaw.api.errors import error_response
from melonclaw.core.config import load_settings
from melonclaw.output.formatting import sanitize_text
from melonclaw.repository import ConversationNotFoundError
from melonclaw.services.speech import (
    MAX_AUDIO_BYTES,
    SpeechError,
    normalize_voice,
    open_tts,
    prepare_speech_text,
    transcribe_audio,
)

router = APIRouter()
MAX_ASR_REQUEST_BYTES = MAX_AUDIO_BYTES + 64 * 1024


class SynthesizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    message_id: UUID
    voice_id: str | None = None


def _speech_error(exc: SpeechError, fallback: str) -> JSONResponse:
    message = sanitize_text(str(exc)) or fallback
    return JSONResponse({"error": message}, status_code=exc.status_code)


async def _bounded_audio_body(request: Request) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_ASR_REQUEST_BYTES:
            raise SpeechError("音频文件过大。", 413)
        yield chunk


async def _read_audio(request: Request, user_id: str) -> bytes:
    declared_size = request.headers.get("content-length")
    if declared_size is not None and int(declared_size) > MAX_ASR_REQUEST_BYTES:
        raise SpeechError("音频文件过大。", 413)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "multipart/form-data":
        raise SpeechError("请上传 WAV 音频文件。", 422)
    try:
        form = await MultiPartParser(
            request.headers, _bounded_audio_body(request), max_files=1, max_fields=1, max_part_size=1024,
        ).parse()
    except (MultiPartException, MultipartParseError):
        raise SpeechError("音频上传格式无效。", 400) from None
    except ClientDisconnect:
        raise SpeechError("语音识别已取消。", 499) from None
    try:
        assert_request_user(form.getlist("user_id"), user_id)
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise SpeechError("请上传 WAV 音频文件。", 422)
        payload = await file.read(MAX_AUDIO_BYTES + 1)
        if len(payload) > MAX_AUDIO_BYTES:
            raise SpeechError("音频文件过大。", 413)
        return payload
    finally:
        await form.close()


@router.post(
    "/api/speech/transcribe",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"multipart/form-data": {"schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }}},
        },
    },
)
async def transcribe_speech(request: Request) -> JSONResponse:
    check_origin(request)
    try:
        async with account_session(request, check_body=False):
            user_id = str(request.state.user.user_id)
        payload = await _read_audio(request, user_id)
        result = await transcribe_audio(
            user_id, payload, load_settings().deepseek_api_key, disconnected=request.is_disconnected,
        )
    except SpeechError as exc:
        return _speech_error(exc, "语音识别失败，请稍后再试。")
    return JSONResponse(result)


async def _assistant_text(request: Request, body: SynthesizeInput, voice: str) -> str:
    chat = get_chat_service(request)
    storage = chat.runtime.storage
    if storage is None:
        raise SpeechError("数据库尚未就绪或结构不匹配，请检查初始化及服务状态。", 503)
    message = await storage.get_visible_message(
        body.conversation_id,
        str(request.state.user.user_id),
        body.message_id,
    )
    return prepare_speech_text(message, voice)


class _SynthesisResponse(Response):
    """从等待上游首包开始监听断开，收到 PCM 后再发送 HTTP 200。"""

    media_type = "application/octet-stream"

    def __init__(self, *, user_id: str, text: str, voice: str, api_key: str) -> None:
        super().__init__()
        self.user_id = user_id
        self.text = text
        self.voice = voice
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with create_task_group() as group:
            async def watch_disconnect() -> None:
                while (await receive())["type"] != "http.disconnect":
                    pass
                group.cancel_scope.cancel()

            group.start_soon(watch_disconnect)
            try:
                await self._send_audio(scope, receive, send)
            finally:
                group.cancel_scope.cancel()

    async def _send_audio(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False
        try:
            async with open_tts(
                user_id=self.user_id, text=self.text, voice=self.voice, api_key=self.api_key,
            ) as audio:
                first = await anext(audio, b"")
                if not first:
                    raise SpeechError("朗读失败，请稍后再试。", 503)

                async def chunks() -> AsyncIterator[bytes]:
                    yield first
                    async for chunk in audio:
                        yield chunk

                response = StreamingResponse(
                    chunks(),
                    media_type=self.media_type,
                    headers={
                        "Cache-Control": "no-store",
                        "X-Audio-Format": "pcm",
                        "X-Audio-Sample-Rate": "24000",
                        "X-Audio-Channels": "1",
                        "X-Audio-Bit-Depth": "16",
                    },
                )
                started = True
                # 上面的监听任务独占 ASGI receive，避免两个消费者争抢断开事件。
                await response.stream_response(send)
        except SpeechError as exc:
            if started:
                # 音频已经开始，提前结束 HTTP 流由前端识别为失败。
                raise
            await _speech_error(exc, "朗读失败，请稍后再试。")(scope, receive, send)


@router.post("/api/speech/synthesize", response_model=None)
async def synthesize_speech(request: Request, body: SynthesizeInput) -> Response:
    check_origin(request)
    try:
        async with account_session(request):
            voice = normalize_voice(body.voice_id)
            text = await _assistant_text(request, body, voice)
            user_id = str(request.state.user.user_id)
    except ConversationNotFoundError as exc:
        return error_response(exc)
    except SpeechError as exc:
        return _speech_error(exc, "朗读失败，请稍后再试。")
    return _SynthesisResponse(
        user_id=user_id, text=text, voice=voice, api_key=load_settings().deepseek_api_key,
    )
