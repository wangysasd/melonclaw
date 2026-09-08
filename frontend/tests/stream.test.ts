import { describe, expect, it, vi } from "vitest";
import { streamRequest } from "../src/api/stream";

describe("SSE transport", () => {
  it("handles UTF-8, heartbeats and CRLF boundaries split across chunks", async () => {
    const encoder = new TextEncoder();
    const data = encoder.encode(': keep-alive\r\n\r\ndata: {"type":"text","text":"你好"}\r\n\r\ndata: {"type":"done"}\n\n');
    const body = new ReadableStream({ start(controller) { for (const byte of data) controller.enqueue(Uint8Array.of(byte)); controller.close(); } });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body));
    const onEvent = vi.fn(); await streamRequest("/test", {}, { onEvent });
    expect(onEvent.mock.calls.map(([event]) => event)).toEqual([{ type: "text", text: "你好" }, { type: "done" }]);
  });
  it("surfaces malformed frames rather than silently showing a complete answer", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('data: {broken}\n\n'));
    await expect(streamRequest("/test", {}, { onEvent: vi.fn() })).rejects.toThrow("无法解析");
  });
});
