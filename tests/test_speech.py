"""语音转写代理的校验、凭据边界和上游错误映射。"""

from __future__ import annotations

import asyncio
import json
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from httpx import AsyncClient as ASGIClient

from melonclaw.api.app import create_app
from melonclaw.core.auth_token import issue_token
from melonclaw.core.config import load_settings
from melonclaw.repository import ConversationNotFoundError
from melonclaw.services import speech
from melonclaw.services.speech import SpeechError


def _speech_token() -> str:
    settings = load_settings()
    return issue_token("alice", settings.jwt_secret, settings.token_ttl_hours)


def _speech_authorization() -> tuple[bytes, bytes]:
    return b"authorization", f"Bearer {_speech_token()}".encode()


def wav_bytes(
    *,
    seconds: float = 0.1,
    rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
    audio_format: int = 1,
) -> bytes:
    width = max(bits // 8, 1)
    frames = int(seconds * rate)
    payload = b"\x00" * frames * channels * width
    fmt = struct.pack(
        "<HHIIHH",
        audio_format,
        channels,
        rate,
        rate * channels * width,
        channels * width,
        bits,
    )

    def chunk(name: bytes, body: bytes) -> bytes:
        pad = b"\x00" if len(body) % 2 else b""
        return name + struct.pack("<I", len(body)) + body + pad

    body = b"WAVE" + chunk(b"fmt ", fmt) + chunk(b"data", payload)
    return b"RIFF" + struct.pack("<I", len(body)) + body


class StubClient:
    requests: list[tuple] = []
    created: list[dict] = []
    responder = None

    def __init__(self, **kwargs):
        self.created.append(kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, headers=None, files=None):
        self.requests.append((url, headers, files))
        responder = type(self).responder
        if responder is None:
            raise AssertionError("unexpected network call")
        result = responder(url, headers, files)
        if asyncio.iscoroutine(result):
            return await result
        return result


@pytest.fixture(autouse=True)
def _reset_gate():
    speech.GATE.clear()
    speech.TTS_GATE.clear()
    StubClient.requests = []
    StubClient.created = []
    StubClient.responder = None
    yield
    speech.GATE.clear()
    speech.TTS_GATE.clear()


@pytest.fixture
def network(monkeypatch):
    monkeypatch.setattr(speech.httpx, "AsyncClient", StubClient)
    return StubClient


def _ok(_url, _headers, _files):
    return httpx.Response(200, json={"text": "你好", "duration_ms": 3200, "request_id": "upstream"})


def test_invalid_wav_does_not_call_network(network):
    for payload in (b"not-a-wav", wav_bytes(bits=8), wav_bytes(rate=11025), wav_bytes(channels=3), wav_bytes(audio_format=3)):
        with pytest.raises(SpeechError, match="音频无效") as caught:
            asyncio.run(speech.transcribe_audio("alice", payload, "sk-secret-value"))
        assert caught.value.status_code == 400
    assert network.requests == []


def test_oversized_audio_is_rejected_locally(network):
    with pytest.raises(SpeechError, match="音频文件过大") as caught:
        asyncio.run(speech.transcribe_audio("alice", b"R" * (speech.MAX_AUDIO_BYTES + 1), "sk-secret-value"))
    assert caught.value.status_code == 400
    assert network.requests == []


def test_overlong_audio_is_rejected_locally(network):
    with pytest.raises(SpeechError, match="录音时长超过上限") as caught:
        asyncio.run(speech.transcribe_audio("alice", wav_bytes(seconds=121, rate=8000), "sk-secret-value"))
    assert caught.value.status_code == 400
    assert network.requests == []


def test_exact_duration_limit_reaches_upstream(network):
    network.responder = _ok
    result = asyncio.run(speech.transcribe_audio("alice", wav_bytes(seconds=120, rate=8000), "sk-secret-value"))
    assert result == {"text": "你好", "duration_ms": 3200}
    assert len(network.requests) == 1


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 32000, 44100, 48000])
@pytest.mark.parametrize("channels", [1, 2])
def test_asr_forwards_supported_wav_without_reencoding(network, rate, channels):
    network.responder = _ok
    original = wav_bytes(rate=rate, channels=channels)
    asyncio.run(speech.transcribe_audio("alice", original, "test-key"))
    assert network.requests[0][2]["file"] == ("speech.wav", original, "audio/wav")


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"not JSON"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={"text": 1, "duration_ms": 10}),
    httpx.Response(200, json={"text": "hello", "duration_ms": True}),
    httpx.Response(200, json={"text": "hello", "duration_ms": -1}),
    httpx.Response(200, json={"text": "hello"}),
])
def test_asr_rejects_malformed_upstream_success(network, response):
    network.responder = lambda *_args: response
    with pytest.raises(SpeechError) as caught:
        asyncio.run(speech.transcribe_audio("alice", wav_bytes(), "test-key"))
    assert caught.value.status_code == 503


def test_asr_allows_an_empty_transcript(network):
    network.responder = lambda *_args: httpx.Response(200, json={"text": "", "duration_ms": 0})
    assert asyncio.run(speech.transcribe_audio("alice", wav_bytes(), "test-key")) == {
        "text": "", "duration_ms": 0,
    }


@pytest.mark.parametrize("key", ["", "   "])
def test_missing_key_does_not_call_network(network, key):
    with pytest.raises(SpeechError, match="语音识别尚未配置") as caught:
        asyncio.run(speech.transcribe_audio("alice", wav_bytes(), key))
    assert caught.value.status_code == 503
    assert network.requests == []


@pytest.mark.parametrize(("status", "code", "message"), [
    (400, 400, "音频无效"),
    (401, 503, "鉴权失败"),
    (408, 408, "上传超时"),
    (413, 413, "文件过大"),
    (429, 429, "请求过多"),
    (500, 503, "语音识别失败"),
    (503, 503, "服务繁忙"),
    (504, 504, "识别超时"),
])
def test_upstream_status_is_mapped_without_response_body(network, status, code, message):
    network.responder = lambda *_args: httpx.Response(status, text='{"error":"sk-secret-value leaked"}')
    with pytest.raises(SpeechError, match=message) as caught:
        asyncio.run(speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value"))
    assert caught.value.status_code == code
    assert "sk-secret-value" not in str(caught.value)
    assert "leaked" not in str(caught.value)
    assert len(network.requests) == 1


def test_success_hides_key_and_upstream_request_id(network):
    network.responder = _ok
    result = asyncio.run(speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value"))
    assert result == {"text": "你好", "duration_ms": 3200}
    url, headers, files = network.requests[0]
    assert url == speech.ASR_URL
    assert headers["Authorization"] == "Bearer sk-secret-value"
    assert files["file"][0] == "speech.wav"
    assert "sk-secret-value" not in json.dumps(result)
    timeout = network.created[0]["timeout"]
    assert timeout.connect == 10
    assert timeout.read == 180


def test_timeout_does_not_retry_or_echo_detail(network):
    def boom(*_args):
        raise httpx.ReadTimeout("sk-secret-value")

    network.responder = boom
    with pytest.raises(SpeechError, match="识别超时") as caught:
        asyncio.run(speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value"))
    assert caught.value.status_code == 504
    assert "sk-secret-value" not in str(caught.value)
    assert len(network.requests) == 1


def test_asr_connection_failure_is_sanitized(network):
    def fail(*_args):
        raise httpx.ConnectError("private upstream diagnostic")

    network.responder = fail
    with pytest.raises(SpeechError, match="语音识别失败") as caught:
        asyncio.run(speech.transcribe_audio("alice", wav_bytes(), "test-key"))
    assert caught.value.status_code == 503
    assert "private upstream diagnostic" not in str(caught.value)
    assert len(network.requests) == 1


def test_same_user_second_request_is_rejected_and_slot_is_released(network):
    started = asyncio.Event()
    release = asyncio.Event()

    async def hold(*_args):
        if not started.is_set():
            started.set()
            await release.wait()
        return httpx.Response(200, json={"text": "第一段", "duration_ms": 1})

    network.responder = hold

    async def run():
        first = asyncio.create_task(speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value"))
        await started.wait()
        other = await speech.transcribe_audio("bob", wav_bytes(), "sk-secret-value")
        with pytest.raises(SpeechError, match="请求过多") as caught:
            await speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value")
        assert caught.value.status_code == 429
        assert "sk-secret-value" not in str(caught.value)
        release.set()
        done = await first
        return other, done

    other, done = asyncio.run(run())
    assert other == {"text": "第一段", "duration_ms": 1}
    assert done == {"text": "第一段", "duration_ms": 1}
    assert len(network.requests) == 2


def test_disconnect_cancels_upstream_and_allows_the_next_request(network):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def hang(*_args):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    network.responder = hang

    async def disconnected():
        await started.wait()
        return True

    async def run():
        with pytest.raises(SpeechError, match="语音识别已取消") as caught:
            await speech.transcribe_audio(
                "alice", wav_bytes(), "sk-secret-value", disconnected=disconnected,
            )
        assert caught.value.status_code == 499
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        network.responder = _ok
        return await speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value")

    assert asyncio.run(run()) == {"text": "你好", "duration_ms": 3200}


def _client(app, *, signed_in=True):
    headers = {"Authorization": f"Bearer {_speech_token()}"} if signed_in else {}
    return ASGIClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://local.test",
        headers=headers,
    )


def test_asr_total_deadline_cancels_upstream_and_releases_slot(network, monkeypatch):
    cancelled = False

    async def slow(*_args):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    async def run():
        network.responder = slow
        with monkeypatch.context() as config:
            config.setattr(speech, "TOTAL_TIMEOUT_SECONDS", 0.02)
            with pytest.raises(SpeechError, match="识别超时") as caught:
                await speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value")
        assert caught.value.status_code == 504
        assert cancelled
        network.responder = _ok
        return await speech.transcribe_audio("alice", wav_bytes(), "sk-secret-value")

    assert asyncio.run(run()) == {"text": "你好", "duration_ms": 3200}


def test_route_uses_settings_key_and_omits_it_from_response(monkeypatch):
    storage = _storage()
    seen = {}

    async def fake(user_id, payload, api_key, disconnected=None):
        assert storage.guards == 0
        seen.update(user_id=user_id, api_key=api_key, payload=payload)
        return {"text": "你好", "duration_ms": 10}

    _speech_settings(monkeypatch)
    monkeypatch.setattr("melonclaw.api.routes.speech.transcribe_audio", fake)
    app = _app(storage)

    async def run():
        async with _client(app) as client:
            return await client.post("/api/speech/transcribe", files={"file": ("speech.wav", b"wav-bytes")})

    response = asyncio.run(run())
    assert response.json() == {"text": "你好", "duration_ms": 10}
    assert b"sk-route-secret" not in response.content
    assert seen == {"user_id": "alice", "api_key": "sk-route-secret", "payload": b"wav-bytes"}
    assert app.url_path_for("transcribe_speech") == "/api/speech/transcribe"


def test_route_error_omits_secret(monkeypatch):
    async def fake(*_args, **_kwargs):
        raise SpeechError("语音识别鉴权失败。", 503)

    _speech_settings(monkeypatch)
    monkeypatch.setattr("melonclaw.api.routes.speech.transcribe_audio", fake)

    async def run():
        async with _client(_app(_storage())) as client:
            return await client.post("/api/speech/transcribe", files={"file": ("speech.wav", b"wav-bytes")})

    response = asyncio.run(run())
    assert response.status_code == 503
    assert response.json() == {"error": "语音识别鉴权失败。"}
    assert b"sk-route-secret" not in response.content


@pytest.mark.parametrize(("signed_in", "sized", "status", "chunks_read"), [
    (False, False, 401, 0),
    (True, True, 413, 0),
    (True, False, 413, 2),
])
def test_asr_authorizes_before_reading_and_caps_multipart(monkeypatch, signed_in, sized, status, chunks_read):
    monkeypatch.setattr("melonclaw.api.routes.speech.MAX_ASR_REQUEST_BYTES", 256)
    storage = _storage()
    reads = 0

    async def multipart():
        nonlocal reads
        for chunk in (
            b'--speech\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\n\r\n' + b"x" * 100,
            b"x" * 200,
            b"\r\n--speech--\r\n",
        ):
            assert storage.guards == 0
            reads += 1
            yield chunk

    async def run():
        async with _client(_app(storage), signed_in=signed_in) as client:
            headers = {"Content-Type": "multipart/form-data; boundary=speech"}
            if sized:
                headers["Content-Length"] = "400"
            return await client.post("/api/speech/transcribe", headers=headers, content=multipart())

    response = asyncio.run(run())
    assert response.status_code == status
    assert reads == chunks_read


def test_asr_body_user_is_only_an_identity_assertion():
    async def run():
        async with _client(_app(_storage())) as client:
            return await client.post(
                "/api/speech/transcribe",
                data={"user_id": "bob"},
                files={"file": ("speech.wav", b"wav-bytes")},
            )

    assert asyncio.run(run()).status_code == 409


@pytest.mark.parametrize(("headers", "query", "signed_in", "status"), [
    ({}, {}, False, 401),
    ({"Sec-Fetch-Site": "cross-site"}, {}, True, 403),
    ({"X-Melonclaw-User": "bob"}, {}, True, 409),
    ({}, {"user_id": "bob"}, True, 409),
])
def test_both_speech_routes_enforce_session_and_origin(monkeypatch, headers, query, signed_in, status):
    storage = _storage(_answer())
    app = _app(storage)

    def settings():
        pytest.fail("rejected request must not prepare a speech service call")

    monkeypatch.setattr("melonclaw.api.routes.speech.load_settings", settings)

    async def run():
        async with _client(app, signed_in=signed_in) as client:
            common = {"headers": headers, "params": query}
            return [
                await client.post("/api/speech/transcribe", files={"file": ("voice.wav", wav_bytes())}, **common),
                await client.post("/api/speech/synthesize", json=_tts_input(), **common),
            ]

    assert [response.status_code for response in asyncio.run(run())] == [status, status]
    assert storage.seen == {}


@pytest.mark.parametrize(("body", "status"), [
    ({"content": b"{}", "headers": {"Content-Type": "application/json"}}, 422),
    ({"content": b"invalid", "headers": {"Content-Type": "multipart/form-data"}}, 400),
    ({"files": {"other": ("voice.wav", b"wav")}}, 422),
    ({"files": {"file": (None, "not a file")}}, 422),
    ({"files": [("file", ("a.wav", b"a")), ("file", ("b.wav", b"b"))]}, 400),
])
def test_asr_rejects_invalid_multipart_shapes(network, monkeypatch, body, status):
    _speech_settings(monkeypatch)

    async def run():
        async with _client(_app(_storage())) as client:
            return await client.post("/api/speech/transcribe", **body)

    assert asyncio.run(run()).status_code == status
    assert not network.requests


def test_asr_file_limit_independent_of_multipart_overhead(network, monkeypatch):
    _speech_settings(monkeypatch)
    network.responder = _ok
    rate = 48000
    # 44-byte WAV header and four bytes per stereo frame.
    frames = (speech.MAX_AUDIO_BYTES - 44) // 4
    at_limit = wav_bytes(seconds=frames / rate, rate=rate, channels=2)
    assert len(at_limit) == speech.MAX_AUDIO_BYTES

    async def run():
        async with _client(_app(_storage())) as client:
            accepted = await client.post("/api/speech/transcribe", files={"file": ("voice.wav", at_limit)})
            rejected = await client.post("/api/speech/transcribe", files={"file": ("voice.wav", at_limit + b"\x00")})
        return accepted, rejected

    accepted, rejected = asyncio.run(run())
    assert accepted.status_code == 200
    assert rejected.status_code == 413
    assert len(network.requests) == 1


class StreamResponse:
    def __init__(self, status_code, chunks=(), error=b"", hang=False):
        self.status_code = status_code
        self.chunks = list(chunks)
        self.error = error
        self.hang = hang
        self.closed = False

    def aiter_bytes(self):
        async def generate():
            if self.hang:
                await asyncio.Event().wait()
            if self.status_code != 200:
                if self.error:
                    yield self.error
                return
            for chunk in self.chunks:
                yield chunk

        return generate()

    async def aclose(self):
        self.closed = True


class StreamClient:
    requests: list = []
    created: list = []
    responder = None

    def __init__(self, **kwargs):
        self.timeout = kwargs.get("timeout")
        self.closed = False
        type(self).created.append(self)

    def build_request(self, method, url, headers=None, content=None):
        request = SimpleNamespace(method=method, url=url, headers=headers or {}, content=content)
        type(self).requests.append(request)
        return request

    async def send(self, request, stream=False):
        responder = type(self).responder
        if responder is None:
            raise AssertionError("unexpected network call")
        result = responder(request)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def aclose(self):
        self.closed = True


@pytest.fixture
def tts_network(monkeypatch):
    StreamClient.requests = []
    StreamClient.created = []
    StreamClient.responder = None
    monkeypatch.setattr(speech.httpx, "AsyncClient", StreamClient)
    return StreamClient


async def _pcm_open(text, *, user_id="alice", voice="stella", api_key="sk-secret-value"):
    async with speech.open_tts(user_id=user_id, text=text, voice=voice, api_key=api_key) as audio:
        return b"".join([chunk async for chunk in audio])


def test_billed_length_counts_han_as_two():
    assert speech.billed_length("A 1") == 3
    assert speech.billed_length("汉字") == 4
    assert speech.billed_length(chr(0x20000)) == 2
    assert speech.billed_length(chr(0xF900)) == 2
    assert speech.normalize_voice(None) == "stella"


def test_prepare_uses_saved_answer_only():
    message = {
        "role": "assistant",
        "status": "completed",
        "content": " 正文 ",
        "assistant_steps": [{"content": "不要朗读步骤"}],
    }
    assert speech.prepare_speech_text(message, "stella") == " 正文 "
    with pytest.raises(SpeechError, match="没有可朗读的文本") as blank:
        speech.prepare_speech_text({"role": "assistant", "status": "completed", "content": " \n"}, "stella")
    assert blank.value.status_code == 400
    with pytest.raises(SpeechError, match="消息不存在") as missing:
        speech.prepare_speech_text({"role": "user", "status": "completed", "content": "你好"}, "tide")
    assert missing.value.status_code == 404
    with pytest.raises(SpeechError, match="回复尚未结束") as pending:
        speech.prepare_speech_text({"role": "assistant", "status": "pending", "content": "你好"}, "stella")
    assert pending.value.status_code == 400


def test_invalid_voice_and_over_limit_do_not_call_network(tts_network):
    with pytest.raises(SpeechError, match="音色无效") as voice:
        speech.normalize_voice("other")
    assert voice.value.status_code == 400
    with pytest.raises(SpeechError, match="朗读长度上限") as limit:
        asyncio.run(_pcm_open("a" * (speech.MAX_BILLED_CHARS + 1)))
    assert limit.value.status_code == 400
    assert "sk-secret-value" not in str(limit.value)
    assert tts_network.requests == []


def test_body_size_limit_does_not_call_network(tts_network, monkeypatch):
    monkeypatch.setattr(speech, "MAX_TTS_BODY_BYTES", 8)
    with pytest.raises(SpeechError, match="朗读长度上限") as caught:
        asyncio.run(_pcm_open("hello"))
    assert caught.value.status_code == 400
    assert tts_network.requests == []


@pytest.mark.parametrize("key", ["", "   "])
def test_tts_missing_key_does_not_call_network(tts_network, key):
    with pytest.raises(SpeechError, match="语音合成尚未配置") as caught:
        asyncio.run(_pcm_open("你好", api_key=key))
    assert caught.value.status_code == 503
    assert tts_network.requests == []


def test_tts_stream_uses_deepseek_key_and_keeps_text(tts_network):
    tts_network.responder = lambda _request: StreamResponse(200, [b"\x01\x00", b"\x02\x00"])
    assert asyncio.run(_pcm_open("你好")) == b"\x01\x00\x02\x00"
    request = tts_network.requests[0]
    assert request.url == speech.TTS_URL
    assert request.headers["Authorization"] == "Bearer sk-secret-value"
    assert json.loads(request.content) == {
        "text": "你好",
        "voice_id": "stella",
        "format": "pcm",
        "filter_markdown": True,
    }
    assert tts_network.created[0].timeout.connect == 10
    assert "sk-secret-value" not in request.content.decode()


@pytest.mark.parametrize(("status", "code", "message"), [
    (400, 400, "这条回答暂时无法朗读"),
    (401, 503, "语音合成鉴权失败"),
    (429, 429, "朗读请求过多"),
    (500, 503, "朗读失败"),
    (503, 503, "服务繁忙"),
    (504, 504, "朗读超时"),
])
def test_tts_upstream_status_hides_body_and_key(tts_network, status, code, message):
    tts_network.responder = lambda _request: StreamResponse(status, error=b'{"error":"sk-secret-value leaked"}')
    with pytest.raises(SpeechError, match=message) as caught:
        asyncio.run(_pcm_open("你好"))
    assert caught.value.status_code == code
    assert caught.value.status_code != 401
    assert "sk-secret-value" not in str(caught.value)
    assert "leaked" not in str(caught.value)


def test_tts_timeout_does_not_retry(tts_network):
    def boom(_request):
        raise httpx.ReadTimeout("sk-secret-value")

    tts_network.responder = boom
    with pytest.raises(SpeechError, match="朗读超时") as caught:
        asyncio.run(_pcm_open("你好"))
    assert caught.value.status_code == 504
    assert "sk-secret-value" not in str(caught.value)
    assert len(tts_network.requests) == 1


def test_tts_deadline_is_504(tts_network, monkeypatch):
    async def wait_for_headers(_request):
        await asyncio.Event().wait()

    monkeypatch.setattr(speech, "TTS_DEADLINE_SECONDS", 0.02)
    tts_network.responder = wait_for_headers
    with pytest.raises(SpeechError, match="朗读超时") as caught:
        asyncio.run(_pcm_open("你好"))
    assert caught.value.status_code == 504
    assert tts_network.created[0].closed


@pytest.mark.parametrize("phase", ["headers", "first_pcm", "next_pcm", "sending"])
def test_tts_disconnect_closes_upstream_and_releases_slot(tts_network, monkeypatch, phase):
    _speech_settings(monkeypatch)

    async def run():
        waiting = asyncio.Event()
        disconnected = asyncio.Event()
        sent = []
        body = _tts_input()

        class HangingAudio(StreamResponse):
            async def aiter_bytes(self):
                if phase in {"next_pcm", "sending"}:
                    yield b"\x01\x00"
                waiting.set()
                await asyncio.Event().wait()

            async def aclose(self):
                await asyncio.sleep(0)
                await super().aclose()

        hanging = HangingAudio(200)

        async def upstream(_request):
            if phase == "headers":
                waiting.set()
                await asyncio.Event().wait()
            return hanging

        tts_network.responder = upstream
        incoming = [{"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}]

        async def receive():
            if incoming:
                return incoming.pop()
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(event):
            sent.append(event)
            if phase == "sending" and event["type"] == "http.response.body":
                waiting.set()
                await asyncio.Event().wait()

        app = _app(_storage(_answer()))
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "POST", "path": "/api/speech/synthesize", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), _speech_authorization()],
        }
        task = asyncio.create_task(app(scope, receive, send))
        try:
            await asyncio.wait_for(waiting.wait(), 1)
            disconnected.set()
            await asyncio.wait_for(task, 1)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert tts_network.created[0].closed
        if phase != "headers":
            assert hanging.closed
        if phase in {"headers", "first_pcm"}:
            assert not sent
        else:
            assert sent[0]["status"] == 200
        tts_network.responder = lambda _request: StreamResponse(200, [b"\x03\x00"])
        return await _pcm_open("你好")

    assert asyncio.run(run()) == b"\x03\x00"


def test_tts_same_user_and_global_gate(tts_network):
    started = asyncio.Event()
    release = asyncio.Event()

    async def hang(_request):
        started.set()
        await release.wait()
        return StreamResponse(200, [b"\x01\x00"])

    tts_network.responder = hang

    async def run():
        first = asyncio.create_task(_pcm_open("你好"))
        await started.wait()
        with pytest.raises(SpeechError, match="朗读请求过多") as same:
            await _pcm_open("你好")
        speech.TTS_GATE.limit = 1
        with pytest.raises(SpeechError, match="朗读请求过多") as other:
            await _pcm_open("你好", user_id="bob")
        assert same.value.status_code == 429
        assert other.value.status_code == 429
        assert "sk-secret-value" not in str(same.value)
        release.set()
        return await first

    assert asyncio.run(run()) == b"\x01\x00"


def _storage(message=None, error=None):
    storage = SimpleNamespace(guards=0, seen={})

    @asynccontextmanager
    async def account_guard():
        storage.guards += 1
        try:
            yield
        finally:
            storage.guards -= 1

    async def active_user(user_id):
        assert storage.guards == 1
        if user_id == "alice":
            return SimpleNamespace(user_id="alice", tenant_enabled=True)
        return None

    async def get_visible_message(conversation_id, user_id, message_id):
        assert storage.guards == 1
        storage.seen["lookup"] = (str(conversation_id), user_id, str(message_id))
        if error is not None:
            raise error
        return message

    storage.account_guard = account_guard
    storage.active_user = active_user
    storage.get_visible_message = get_visible_message
    return storage


def _app(storage):
    app = create_app()
    app.state.chat = SimpleNamespace(storage=storage, runtime=SimpleNamespace(storage=storage))
    return app


def _speech_settings(monkeypatch):
    monkeypatch.setattr(
        "melonclaw.api.routes.speech.load_settings",
        lambda: SimpleNamespace(deepseek_api_key="sk-route-secret"),
    )


def _answer():
    return {
        "role": "assistant",
        "status": "failed",
        "content": "正文",
        "assistant_steps": [{"content": "不要合成步骤"}],
    }


def _tts_input(voice=None):
    return {"conversation_id": str(uuid4()), "message_id": str(uuid4()), "voice_id": voice}


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_synthesize_route_streams_saved_answer_with_configured_key(tts_network, monkeypatch, status):
    _speech_settings(monkeypatch)
    storage = _storage({**_answer(), "status": status})
    audio = StreamResponse(200, [b"\x01\x00", b"\x02\x00"])

    def upstream(_request):
        assert storage.guards == 0
        return audio

    tts_network.responder = upstream
    body = _tts_input("tide")
    app = _app(storage)

    async def run():
        async with _client(app) as client:
            return await client.post("/api/speech/synthesize", json=body)

    response = asyncio.run(run())
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-audio-sample-rate"] == "24000"
    assert response.headers["x-audio-channels"] == "1"
    assert response.headers["x-audio-bit-depth"] == "16"
    assert response.content == b"\x01\x00\x02\x00"
    assert audio.closed
    assert tts_network.requests[0].headers["Authorization"] == "Bearer sk-route-secret"
    assert json.loads(tts_network.requests[0].content) == {
        "text": "正文", "voice_id": "tide", "format": "pcm", "filter_markdown": True,
    }
    assert storage.seen["lookup"] == (body["conversation_id"], "alice", body["message_id"])
    assert b"sk-route-secret" not in response.content
    assert app.url_path_for("synthesize_speech") == "/api/speech/synthesize"


@pytest.mark.parametrize(("message", "error", "voice", "status", "text"), [
    (None, ConversationNotFoundError(), None, 404, "会话不存在或不属于当前用户。"),
    (None, None, None, 404, "消息不存在。"),
    ({"role": "user", "status": "completed", "content": "用户原文"}, None, None, 404, "消息不存在。"),
    ({"role": "assistant", "status": "pending", "content": "还在写"}, None, None, 400, "回复尚未结束，暂时不能朗读。"),
    ({"role": "assistant", "status": "completed", "content": "  "}, None, None, 400, "没有可朗读的文本。"),
    (None, None, "unknown", 400, "音色无效。"),
])
def test_synthesize_route_rejects_invalid_input(tts_network, message, error, voice, status, text):
    async def run():
        async with _client(_app(_storage(message, error))) as client:
            return await client.post("/api/speech/synthesize", json=_tts_input(voice))

    response = asyncio.run(run())
    assert response.status_code == status
    assert response.json() == {"error": text}
    assert not tts_network.requests


@pytest.mark.parametrize(("field", "value"), [
    ("conversation_id", "not-a-uuid"),
    ("message_id", None),
    ("voice_id", 3),
    ("text", "client-supplied text"),
    ("user_id", "bob"),
])
def test_tts_rejects_invalid_request_schema(tts_network, field, value):
    storage = _storage(_answer())
    body = _tts_input()
    body[field] = value

    async def run():
        async with _client(_app(storage)) as client:
            return await client.post("/api/speech/synthesize", json=body)

    assert asyncio.run(run()).status_code == 422
    assert storage.seen == {}
    assert not tts_network.requests


@pytest.mark.parametrize("text", ["a" * 10000, "字" * 5000])
def test_tts_accepts_exact_billed_character_limit(tts_network, text):
    tts_network.responder = lambda _: StreamResponse(200, [b"\x01\x00"])
    assert asyncio.run(_pcm_open(text)) == b"\x01\x00"
    assert json.loads(tts_network.requests[0].content)["text"] == text


@pytest.mark.parametrize(("upstream_status", "expected_status"), [(200, 503), (401, 503), (429, 429), (504, 504)])
def test_tts_returns_json_if_no_pcm_was_sent(tts_network, monkeypatch, upstream_status, expected_status):
    _speech_settings(monkeypatch)
    # For non-200 responses, ensure the proxy does not wait for the error body.
    audio = StreamResponse(upstream_status, hang=upstream_status != 200)
    tts_network.responder = lambda _: audio

    async def run():
        async with _client(_app(_storage(_answer()))) as client:
            return await asyncio.wait_for(client.post("/api/speech/synthesize", json=_tts_input()), 2)

    response = asyncio.run(run())
    assert response.status_code == expected_status
    assert response.headers["content-type"] == "application/json"
    assert "error" in response.json()
    assert audio.closed and tts_network.created[0].closed


def test_tts_read_failure_after_first_pcm_leaves_http_stream_incomplete(tts_network, monkeypatch):
    _speech_settings(monkeypatch)

    class BrokenAudio(StreamResponse):
        async def aiter_bytes(self):
            yield b"\x01\x00"
            raise httpx.ReadError("private upstream diagnostic")

    broken = BrokenAudio(200)
    tts_network.responder = lambda _: broken
    sent = []

    async def run():
        incoming = [{"type": "http.request", "body": json.dumps(_tts_input()).encode(), "more_body": False}]

        async def receive():
            if incoming:
                return incoming.pop()
            await asyncio.Event().wait()

        async def send(event):
            sent.append(event)

        app = _app(_storage(_answer()))
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "method": "POST", "path": "/api/speech/synthesize", "query_string": b"",
            "headers": [(b"content-type", b"application/json"), _speech_authorization()],
        }
        with pytest.RaisesGroup(SpeechError, match="unhandled errors in a TaskGroup") as caught:
            await asyncio.wait_for(app(scope, receive, send), 2)
        assert "private upstream diagnostic" not in str(caught.value.exceptions[0])
        assert broken.closed and tts_network.created[0].closed
        tts_network.responder = lambda _: StreamResponse(200, [b"\x02\x00"])
        assert await _pcm_open("下一条") == b"\x02\x00"

    asyncio.run(run())
    assert len(sent) == 2
    assert sent[0]["status"] == 200
    assert sent[1] == {"type": "http.response.body", "body": b"\x01\x00", "more_body": True}
