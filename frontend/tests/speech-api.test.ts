import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setRequestUser } from "../src/api/auth";
import { synthesizeSpeech, transcribeSpeech } from "../src/api/speech";

const fetchMock = vi.fn<typeof fetch>();
const wav = new Blob(["sample"], { type: "audio/wav" });
const synthesis = { conversationId: "conversation", messageId: "message", voiceId: "tide" } as const;

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  setRequestUser("alice");
  fetchMock.mockReset();
});

afterEach(() => {
  localStorage.removeItem("melonclaw.access-token");
  setRequestUser("");
  vi.unstubAllGlobals();
});

describe("speech HTTP client", () => {
  it("attaches the stored bearer token to speech uploads and synthesis", async () => {
    localStorage.setItem("melonclaw.access-token", "speech-token");
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ text: "你好", duration_ms: 1 })));
    await transcribeSpeech(wav, new AbortController().signal);
    expect(fetchMock.mock.calls[0][1]?.headers).toEqual({
      Authorization: "Bearer speech-token",
      "X-Melonclaw-User": "alice",
    });
    fetchMock.mockClear();
    fetchMock.mockResolvedValue(new Response(Uint8Array.of(1, 2)));
    await synthesizeSpeech(synthesis, new AbortController().signal);
    expect(fetchMock.mock.calls[0][1]?.headers).toEqual({
      Authorization: "Bearer speech-token",
      "Content-Type": "application/json",
      "X-Melonclaw-User": "alice",
    });
  });

  it("uploads one multipart file with the identity assertion and bearer token", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({
      text: "测试文本", duration_ms: 320, request_id: "unused",
    })));
    const { signal } = new AbortController();
    await expect(transcribeSpeech(wav, signal)).resolves.toEqual({ text: "测试文本", duration_ms: 320 });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/speech\/transcribe$/);
    expect(options).toMatchObject({ method: "POST", cache: "no-store", signal });
    expect(options?.headers).toEqual({ "X-Melonclaw-User": "alice" });
    const form = options?.body as FormData;
    expect([...form.keys()]).toEqual(["file"]);
    expect(form.get("file")).toMatchObject({ name: "speech.wav", type: "audio/wav", size: wav.size });
  });

  it("requests speech using only message identifiers and voice, then returns PCM bytes", async () => {
    const pcm = Uint8Array.of(0, 64, 0, 128);
    fetchMock.mockResolvedValue(new Response(pcm, { headers: { "Content-Type": "application/octet-stream" } }));
    const { signal } = new AbortController();
    const stream = await synthesizeSpeech(synthesis, signal);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/speech\/synthesize$/);
    expect(options).toMatchObject({ method: "POST", cache: "no-store", signal });
    expect(options?.headers).toEqual({ "Content-Type": "application/json", "X-Melonclaw-User": "alice" });
    expect(JSON.parse(options?.body as string)).toEqual({
      conversation_id: "conversation", message_id: "message", voice_id: "tide",
    });
    expect(new Uint8Array(await new Response(stream).arrayBuffer())).toEqual(pcm);
  });

  it.each([null, { text: "missing duration" }, { text: 12, duration_ms: 0 }])(
    "rejects a malformed transcription response: %j",
    async (body) => {
      fetchMock.mockResolvedValue(new Response(JSON.stringify(body)));
      await expect(transcribeSpeech(wav, new AbortController().signal)).rejects.toThrow();
    },
  );

  it("rejects success without an audio response body", async () => {
    fetchMock.mockResolvedValue(new Response(null));
    await expect(synthesizeSpeech(synthesis, new AbortController().signal)).rejects.toMatchObject({
      status: 0, message: "朗读失败，请稍后再试",
    });
  });

  const sendSpeech = [
    ["ASR", (signal: AbortSignal) => transcribeSpeech(wav, signal)],
    ["TTS", (signal: AbortSignal) => synthesizeSpeech(synthesis, signal)],
  ] as const;

  it.each(sendSpeech)("%s preserves cancellation and normalizes network failures", async (_name, send) => {
    const { signal } = new AbortController();
    const aborted = new DOMException("aborted", "AbortError");
    fetchMock.mockRejectedValueOnce(aborted).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(send(signal)).rejects.toBe(aborted);
    await expect(send(signal)).rejects.toMatchObject({ status: 0, message: expect.stringContaining("无法连接") });
  });

  it.each(sendSpeech)("%s distinguishes app session expiry from an upstream credential failure", async (_name, send) => {
    let expired = 0;
    const onExpired = () => { expired += 1; };
    window.addEventListener("melonclaw-auth-expired", onExpired);
    try {
      const { signal } = new AbortController();
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ error: "语音鉴权失败" }), { status: 503 }));
      await expect(send(signal)).rejects.toMatchObject({ status: 503, message: "语音鉴权失败" });
      expect(expired).toBe(0);
      fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "请先登录。" }), { status: 401 }));
      await expect(send(signal)).rejects.toMatchObject({ status: 401 });
      expect(expired).toBe(1);
    } finally {
      window.removeEventListener("melonclaw-auth-expired", onExpired);
    }
  });
});
