import { afterEach, describe, expect, it, vi } from "vitest";

import { CLIENT_CAPABILITIES, sendMessageStream } from "../src/api/stream";

/**
 * 能力协商：浏览器必须随消息声明 user_input_v1，服务端才会注入 ask_user。
 * 少了这一步，Agent 会用一张旧客户端渲染不出来的问题卡片把会话挂住。
 */

function sseResponse(payload: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${payload}\n\n`));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sendMessageStream", () => {
  it("declares the user_input_v1 capability on every message", async () => {
    const fetchMock = vi.fn(async () =>
      sseResponse(JSON.stringify({ type: "done", terminal_reason: "completed" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await sendMessageStream(
      "c1",
      { userId: "u1", requestId: "r1", content: "你好" },
      { onEvent: () => undefined },
    );

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(JSON.parse(String(init?.body)).capabilities).toEqual(["user_input_v1"]);
    expect(CLIENT_CAPABILITIES).toContain("user_input_v1");
  });
});
