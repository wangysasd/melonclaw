import { API_BASE_URL, ApiError, parseErrorResponse } from "./client";
import type { SendApprovalInput, SendMessageInput, StreamEvent } from "../types/api";

/**
 * SSE 流客户端：POST + fetch ReadableStream 手工解析。
 *
 * 后端帧格式为 `id: N\ndata: {json}\n\n`，空闲时发送 `: keep-alive` 注释帧；
 * 注释帧不产生事件，`data:` 帧解析为 StreamEvent 后交给 onEvent。
 * 流式端点在准备阶段（校验/幂等/抢锁）可能返回非 2xx，此时按 REST 错误归一化。
 */
export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void;
  signal?: AbortSignal;
}

export function sendMessageStream(
  conversationId: string,
  input: SendMessageInput,
  handlers: StreamHandlers,
): Promise<void> {
  return streamRequest(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      user_id: input.userId,
      tenant_id: input.tenantId ?? null,
      request_id: input.requestId,
      content: input.content,
    },
    handlers,
  );
}

export function sendApprovalStream(
  conversationId: string,
  input: SendApprovalInput,
  handlers: StreamHandlers,
): Promise<void> {
  return streamRequest(
    `/api/conversations/${encodeURIComponent(conversationId)}/approval`,
    {
      user_id: input.userId,
      tenant_id: input.tenantId ?? null,
      decisions: input.decisions,
    },
    handlers,
  );
}

export async function streamRequest(
  path: string,
  body: unknown,
  { onEvent, signal }: StreamHandlers,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(0, "无法连接到 MelonClaw 服务，请检查网络或服务状态。");
  }
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
  if (!response.body) {
    throw new ApiError(0, "服务器没有返回可读取的响应流。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let boundary = /\r?\n\r?\n/.exec(buffer);
      while (boundary) {
        dispatchFrame(buffer.slice(0, boundary.index), onEvent);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        boundary = /\r?\n\r?\n/.exec(buffer);
      }
    }
    // 兼容流末尾缺少空行终止符的最后一段。
    if (buffer.trim()) {
      dispatchFrame(buffer, onEvent);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

function dispatchFrame(frame: string, onEvent: (event: StreamEvent) => void): void {
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) {
      // 空行或 keep-alive 注释帧。
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
    // `id:` 帧为服务端序号，前端不需要。
  }
  if (dataLines.length === 0) {
    return;
  }
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    throw new Error("无法解析助手的响应，请重试。");
  }
  if (
    payload &&
    typeof payload === "object" &&
    typeof (payload as { type?: unknown }).type === "string"
  ) {
    onEvent(payload as StreamEvent);
  }
}
