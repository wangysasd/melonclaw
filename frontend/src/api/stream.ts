import { API_BASE_URL, ApiError, parseErrorResponse } from "./client";
import type {
  SendApprovalInput,
  SendMessageInput,
  SendUserInputInput,
  StreamEvent,
} from "../types/api";

/**
 * SSE 流客户端：POST + fetch ReadableStream 手工解析。
 *
 * 后端帧格式为 `id: N\ndata: {json}\n\n`，空闲时发送 `: keep-alive` 注释帧；
 * 注释帧不产生事件，`data:` 帧解析为 StreamEvent 后交给 onEvent。
 * 流式端点在准备阶段（校验/幂等/抢锁）可能返回非 2xx，此时按 REST 错误归一化。
 */
export interface StreamHandlers {
  /** eventId 是服务端 `id:` 帧序号（单条流内单调递增），用于丢弃重复投递。 */
  onEvent: (event: StreamEvent, eventId?: number) => void;
  signal?: AbortSignal;
}

/**
 * 浏览器声明的能力清单，随每条消息一起发送。
 *
 * 服务端只给声明了 `user_input_v1` 的客户端注入 ask_user 工具：旧客户端渲染
 * 不出问题卡片，拿到提问工具只会把会话挂死在一张看不见的卡片上。
 */
export const CLIENT_CAPABILITIES = ["user_input_v1"];

export function sendMessageStream(
  conversationId: string,
  input: SendMessageInput,
  handlers: StreamHandlers,
): Promise<void> {
  return streamRequest(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      user_id: input.userId,
      request_id: input.requestId,
      content: input.content,
      model_id: input.modelId ?? null,
      skill_id: input.skillId ?? null,
      attachment_ids: input.attachmentIds ?? [],
      capabilities: CLIENT_CAPABILITIES,
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
      approval_batch_id: input.approvalBatchId,
      assistant_message_id: input.assistantMessageId,
      decisions: input.decisions,
    },
    handlers,
  );
}

export function sendUserInputStream(
  conversationId: string,
  input: SendUserInputInput,
  handlers: StreamHandlers,
): Promise<void> {
  return streamRequest(
    `/api/conversations/${encodeURIComponent(conversationId)}/user-input`,
    {
      user_id: input.userId,
      interaction_id: input.interactionId,
      assistant_message_id: input.assistantMessageId,
      decision_request_id: input.decisionRequestId,
      answer: input.answer,
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
      credentials: "include",
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
      const { done, value } = await reader.read().catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") throw error;
        throw new ApiError(0, "网络连接中断，请同步会话确认执行结果。");
      });
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

function dispatchFrame(
  frame: string,
  onEvent: (event: StreamEvent, eventId?: number) => void,
): void {
  const dataLines: string[] = [];
  let eventId: number | undefined;
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) {
      // 空行或 keep-alive 注释帧。
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
      continue;
    }
    if (line.startsWith("id:")) {
      const parsed = Number(line.slice(3).trim());
      eventId = Number.isFinite(parsed) ? parsed : undefined;
    }
  }
  if (dataLines.length === 0) {
    return;
  }
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    throw new Error("无法解析助手的响应，请同步会话确认执行结果。");
  }
  if (
    payload &&
    typeof payload === "object" &&
    typeof (payload as { type?: unknown }).type === "string"
  ) {
    onEvent(payload as StreamEvent, eventId);
  }
}
