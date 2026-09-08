import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionContextValue } from "../src/state/session";
import type { ConversationHistory, StreamEvent } from "../src/types/api";
import { INITIAL_CHAT_STATE, reducer, useChatStream } from "../src/hooks/useChatStream";
import { getConversationHistory } from "../src/api/client";
import { sendApprovalStream, sendMessageStream } from "../src/api/stream";

const mocks = vi.hoisted(() => ({ session: {} as SessionContextValue, message: { error: vi.fn() } }));
vi.mock("../src/state/session", () => ({ useSession: () => mocks.session }));
vi.mock("antd", () => ({ App: { useApp: () => ({ message: mocks.message }) } }));
vi.mock("../src/api/client", () => ({ getConversationHistory: vi.fn() }));
vi.mock("../src/api/stream", () => ({ sendMessageStream: vi.fn(), sendApprovalStream: vi.fn() }));
const scroll = { isNearBottom: () => true, scrollToBottom: vi.fn() };
const emptyHistory: ConversationHistory = { conversation: { id: "c1", title: "Test" }, items: [], pending_approval: null };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; }

beforeEach(() => {
  vi.clearAllMocks();
  mocks.session = {
    conversationId: "c1", userId: "u", tenantId: "t", projectId: "p", epoch: 1,
    contextReady: true, busy: false, conversationCreating: false, projects: [{ id: "p", name: "p" }], status: { status: "ready" },
    setBusy: vi.fn((value) => { mocks.session.busy = value; }),
    setRunStatus: vi.fn((value) => { mocks.session.runStatus = value; }),
    attachStream: vi.fn(), refreshConversations: vi.fn().mockResolvedValue(undefined),
  } as unknown as SessionContextValue;
  vi.mocked(getConversationHistory).mockResolvedValue(emptyHistory);
});

describe("chat run lifecycle", () => {
  it("ignores a late history response after sending", async () => {
    const history = deferred<ConversationHistory>();
    vi.mocked(getConversationHistory).mockReturnValue(history.promise);
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: "u1", message_id: "a1" });
      onEvent({ type: "text", text: "你好" });
      onEvent({ type: "approval_required", request: { id: "approval", actions: [] } });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await act(() => result.current.sendMessage("test"));
    await act(async () => { history.resolve(emptyHistory); });
    expect(result.current.state.messages.map((m) => m.content)).toEqual(["test", "你好"]);
    expect(result.current.state.historyLoading).toBe(false);
    expect(result.current.state.messages[1].status).toBe("interrupted");
    expect(mocks.session.runStatus).toBe("waiting");
  });
  it("ends streaming on premature EOF and retains the partial answer", async () => {
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => { onEvent({ type: "text", text: "partial" }); });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    // A real server sends message_started before text.
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: "u1", message_id: "a1" });
      onEvent({ type: "text", text: "partial" });
    });
    await act(() => result.current.sendMessage("test"));
    expect(result.current.state.messages[1]).toMatchObject({ content: "partial", status: "failed" });
    expect(result.current.state.error).toContain("连接意外结束");
    expect(mocks.session.busy).toBe(false); expect(mocks.session.runStatus).toBe("failed");
  });
  it("does not let done erase a preceding error", async () => {
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({ type: "error", message: "网络中断" }); onEvent({ type: "done", terminal_reason: "transport_error" });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    await act(() => result.current.sendMessage("test"));
    expect(result.current.state.error).toBe("网络中断"); expect(mocks.session.runStatus).toBe("failed");
  });
  it("clears approval only after resume is accepted and can show a subsequent interrupt", async () => {
    const approval = { id: "first", actions: [] };
    vi.mocked(getConversationHistory).mockResolvedValue({ ...emptyHistory, pending_approval: approval, items: [{ id: "a1", role: "assistant", content: "", status: "interrupted" }] });
    const stream = deferred<void>(); let emit!: (event: StreamEvent) => void;
    vi.mocked(sendApprovalStream).mockImplementation((_id, _input, { onEvent }) => { emit = onEvent; return stream.promise; });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.approval?.id).toBe("first"));
    let task!: Promise<void>; act(() => { task = result.current.submitApproval([{ type: "reject" }]); });
    expect(result.current.state.approval?.id).toBe("first");
    act(() => emit({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: null, message_id: "a1", resuming: true }));
    expect(result.current.state.approval).toBeNull(); expect(result.current.state.messages[0].status).toBe("streaming");
    await act(async () => { emit({ type: "approval_required", request: { id: "second", actions: [] } }); stream.resolve(); await task; });
    expect(result.current.state.approval?.id).toBe("second");
  });
  it("does not mark an old completed answer as failed when a new request fails before starting", () => {
    const state = { ...INITIAL_CHAT_STATE, messages: [{ id: "old", role: "assistant" as const, content: "done", status: "completed" as const, markdown: true, events: [] }] };
    expect(reducer(state, { type: "streamFailed", message: "draft", error: "offline" }).messages[0].status).toBe("completed");
  });
  it("an old stream cannot detach the new conversation's controller", async () => {
    const first = deferred<void>(); const second = deferred<void>();
    vi.mocked(sendMessageStream).mockImplementationOnce(() => first.promise).mockImplementationOnce(() => second.promise);
    const { result, rerender } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let firstTask!: Promise<void>; act(() => { firstTask = result.current.sendMessage("one"); });
    mocks.session = { ...mocks.session, conversationId: "c2", epoch: 2, busy: false };
    rerender(); await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let secondTask!: Promise<void>; act(() => { secondTask = result.current.sendMessage("two"); });
    const calls = vi.mocked(mocks.session.attachStream).mock.calls.length;
    await act(async () => { first.resolve(); await firstTask; });
    expect(vi.mocked(mocks.session.attachStream).mock.calls).toHaveLength(calls);
    await act(async () => { second.resolve(); await secondTask; });
  });
});
