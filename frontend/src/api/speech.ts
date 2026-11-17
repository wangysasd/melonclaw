import { identityHeaders } from "./auth";
import { API_BASE_URL, ApiError, parseErrorResponse } from "./client";

export interface Transcript {
  text: string;
  duration_ms: number;
}

/** 上传整段 WAV。不设置 Content-Type，让浏览器写入 multipart 边界。 */
export async function transcribeSpeech(audio: Blob, signal: AbortSignal): Promise<Transcript> {
  const form = new FormData();
  form.append("file", audio, "speech.wav");
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/speech/transcribe`, {
      method: "POST",
      credentials: "include",
      headers: identityHeaders(),
      body: form,
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "无法连接到 MelonClaw 服务，请检查网络或服务状态。");
  }
  if (!response.ok) throw await parseErrorResponse(response);
  const body = await response.json() as Partial<Transcript>;
  if (typeof body.text !== "string" || typeof body.duration_ms !== "number") {
    throw new ApiError(0, "语音识别失败，请稍后再试");
  }
  return { text: body.text, duration_ms: body.duration_ms };
}

export interface SynthesisRequest {
  conversationId: string;
  messageId: string;
  voiceId: "stella" | "tide";
}

/** 请求已保存回复的 PCM 流。浏览器不接触上游地址或 Key。 */
export async function synthesizeSpeech(
  input: SynthesisRequest,
  signal: AbortSignal,
): Promise<ReadableStream<Uint8Array>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/speech/synthesize`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...identityHeaders() },
      body: JSON.stringify({
        conversation_id: input.conversationId,
        message_id: input.messageId,
        voice_id: input.voiceId,
      }),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "无法连接到 MelonClaw 服务，请检查网络或服务状态。");
  }
  if (!response.ok) throw await parseErrorResponse(response);
  if (!response.body) throw new ApiError(0, "朗读失败，请稍后再试");
  return response.body;
}
