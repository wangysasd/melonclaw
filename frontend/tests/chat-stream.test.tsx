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
    selectedModelId: "system:deepseek:flash",
    modelOptions: [{ id: "system:deepseek:flash", display_name: "DeepSeek Flash", source: "system", provider: "deepseek", model: "deepseek-v4-flash", available: true, is_default: true }],
    contextReady: true, busy: false, conversationCreating: false, projects: [{ id: "p", name: "p" }], status: { status: "ready" },
    setBusy: vi.fn((value) => { mocks.session.busy = value; }),
    setRunStatus: vi.fn((value) => { mocks.session.runStatus = value; }),
    attachStream: vi.fn(), refreshConversations: vi.fn().mockResolvedValue(undefined),
  } as unknown as SessionContextValue;
  vi.mocked(getConversationHistory).mockResolvedValue(emptyHistory);
});

describe("chat run lifecycle", () => {
  it("sends the selected model ID and records it on the streaming reply", async () => {
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({
        type: "message_started",
        conversation_id: "c1",
        request_id: "r",
        user_message_id: "u1",
        message_id: "a1",
        model: {
          id: "system:deepseek:flash",
          display_name: "DeepSeek Flash",
          provider: "deepseek",
          model: "deepseek-v4-flash",
        },
      });
      onEvent({ type: "completed", message_id: "a1", content: "你好" });
      onEvent({ type: "done", terminal_reason: "completed" });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    await act(() => result.current.sendMessage("test"));
    expect(vi.mocked(sendMessageStream).mock.calls[0]?.[1]).toMatchObject({
      modelId: "system:deepseek:flash",
    });
    expect(result.current.state.messages[1]).toMatchObject({
      content: "你好",
      model: { id: "system:deepseek:flash", model: "deepseek-v4-flash" },
    });
  });

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
  it("shows tool selection while hiding the selector JSON", async () => {
    const stream = deferred<void>(); let emit!: (event: StreamEvent) => void;
    vi.mocked(sendMessageStream).mockImplementation((_id, _input, { onEvent }) => {
      emit = onEvent;
      return stream.promise;
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let task!: Promise<void>;
    act(() => { task = result.current.sendMessage("test"); });
    await waitFor(() => expect(emit).toBeDefined());
    act(() => emit({ type: "text", text: '{"tools":' }));
    expect(mocks.session.runStatus).toBe("selecting_tools");
    act(() => emit({ type: "text", text: "[]}" }));
    expect(result.current.state.messages[1].content).toBe("");
    expect(mocks.session.runStatus).toBe("selecting_tools");
    act(() => emit({ type: "tool_call", name: "example", args: {}, status: "started" }));
    expect(mocks.session.runStatus).toBe("processing");
    act(() => emit({ type: "completed", message_id: "a1", content: '{"tools":[]}' }));
    await act(async () => { emit({ type: "done", terminal_reason: "completed" }); stream.resolve(); await task; });
    expect(result.current.state.messages[1].content).toBe("");
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
  it("forces history sync after returning to a conversation with a stale stream ref", async () => {
    const stream = deferred<void>();
    vi.mocked(sendMessageStream).mockImplementation(() => stream.promise);
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let task!: Promise<void>;
    act(() => { task = result.current.sendMessage("test"); });
    await waitFor(() => expect(vi.mocked(sendMessageStream)).toHaveBeenCalled());
    const initialHistoryCalls = vi.mocked(getConversationHistory).mock.calls.length;
    act(() => result.current.reloadHistory());
    await waitFor(() => expect(vi.mocked(getConversationHistory).mock.calls.length).toBe(initialHistoryCalls + 1));
    stream.resolve();
    await act(async () => { await task; });
  });
  it("keeps the conversation stream alive while another conversation is selected", async () => {
    const stream = deferred<void>(); let streamSignal!: AbortSignal; let emit!: (event: StreamEvent) => void;
    let historyCalls = 0;
    vi.mocked(getConversationHistory).mockImplementation(async () => {
      historyCalls += 1;
      return historyCalls >= 3
        ? { ...emptyHistory, items: [{ id: "a1", role: "assistant", content: "", status: "pending" }] }
        : emptyHistory;
    });
    vi.mocked(sendMessageStream).mockImplementation((_id, _input, { onEvent, signal }) => {
      emit = onEvent;
      streamSignal = signal!;
      return stream.promise;
    });
    const { result, rerender } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let task!: Promise<void>;
    act(() => { task = result.current.sendMessage("test"); });
    await waitFor(() => expect(streamSignal).toBeDefined());
    act(() => {
      mocks.session = { ...mocks.session, conversationId: "c2", epoch: 2 };
      rerender();
    });
    expect(streamSignal.aborted).toBe(false);
    act(() => {
      mocks.session = { ...mocks.session, conversationId: "c1", epoch: 3 };
      rerender();
    });
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    act(() => emit({ type: "text", text: "继续输出" }));
    expect(result.current.state.messages.at(-1)?.content).toBe("继续输出");
    act(() => emit({ type: "completed", message_id: "a1", content: "继续输出" }));
    act(() => emit({ type: "done", terminal_reason: "completed" }));
    stream.resolve();
    await act(async () => { await task; });
  });
});
