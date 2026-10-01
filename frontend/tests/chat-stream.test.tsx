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
vi.mock("../src/api/stream", () => ({ sendMessageStream: vi.fn(), sendApprovalStream: vi.fn(), sendUserInputStream: vi.fn() }));
const scroll = { isNearBottom: () => true, scrollToBottom: vi.fn() };
const emptyHistory: ConversationHistory = { conversation: { id: "c1", title: "Test" }, items: [], pending_approval: null };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; }

beforeEach(() => {
  vi.clearAllMocks();
  mocks.session = {
    conversationId: "c1", userId: "u", projectId: "p", epoch: 1,
    selectedModelId: "custom:deepseek-flash",
    modelOptions: [{ id: "custom:deepseek-flash", display_name: "DeepSeek Flash", source: "system", provider: "deepseek", provider_key: "deepseek", model: "deepseek-v4-flash", available: true, is_default: true }],
    contextReady: true, busy: false, conversationCreating: false, projects: [{ id: "p", name: "p" }], status: { status: "ready" },
    runningConversationIds: [],
    setBusy: vi.fn((value) => { mocks.session.busy = value; }),
    setRunStatus: vi.fn((value) => { mocks.session.runStatus = value; }),
    markConversationRunning: vi.fn((id: string) => {
      if (!mocks.session.runningConversationIds.includes(id)) mocks.session.runningConversationIds.push(id);
      mocks.session.busy = true;
    }),
    markConversationIdle: vi.fn((id: string) => {
      mocks.session.runningConversationIds = mocks.session.runningConversationIds.filter((item) => item !== id);
      if (mocks.session.runningConversationIds.length === 0) mocks.session.busy = false;
    }),
    attachStream: vi.fn(), refreshConversations: vi.fn().mockResolvedValue(undefined),
    markConversationSubmitted: vi.fn(), identifyConversationSubmission: vi.fn(), cancelConversationSubmission: vi.fn(),
    markConversationStarted: vi.fn(),
  } as unknown as SessionContextValue;
  vi.mocked(getConversationHistory).mockResolvedValue(emptyHistory);
});

describe("chat run lifecycle", () => {
  it("does not put an old conversation's history error on a new blank page", () => {
    const blank = reducer({ ...INITIAL_CHAT_STATE, conversationId: null }, { type: "historyFailed", conversationId: "c1", error: "旧会话加载失败" });
    expect(blank.error).toBeNull();
  });

  it("reveals a new conversation on send before the first server event", async () => {
    mocks.session.conversationId = null;
    mocks.session.projectId = "";
    mocks.session.draftConversationId = null;
    const creation = deferred<void>();
    mocks.session.ensureConversation = vi.fn(async () => {
      await creation.promise;
      mocks.session.conversationId = "c2";
      mocks.session.draftConversationId = "c2";
      return { id: "c2", project_id: null };
    });
    mocks.session.markConversationStarted = vi.fn();
    let finishStream!: () => void;
    vi.mocked(sendMessageStream).mockImplementation((_id, _input, { onEvent }) => new Promise<void>((resolve) => {
      finishStream = () => {
        onEvent({ type: "message_started", conversation_id: "c2", request_id: "r2", user_message_id: "u2", message_id: "a2" });
        onEvent({ type: "completed", message_id: "a2", content: "收到", assistant_steps: [] });
        onEvent({ type: "done", terminal_reason: "completed" });
        resolve();
      };
    }));
    const { result } = renderHook(() => useChatStream({ scroll }));
    await act(async () => { await Promise.resolve(); });
    let sending!: Promise<void>;
    act(() => { sending = result.current.sendMessage("第一条消息"); });
    expect(mocks.session.markConversationSubmitted).toHaveBeenCalledWith(expect.stringMatching(/^local:/), "", "第一条消息", true);
    expect(mocks.session.identifyConversationSubmission).not.toHaveBeenCalled();
    await act(async () => { creation.resolve(); await Promise.resolve(); });
    await waitFor(() => expect(mocks.session.identifyConversationSubmission).toHaveBeenCalledWith(expect.stringMatching(/^local:/), "c2"));
    expect(mocks.session.markConversationStarted).not.toHaveBeenCalled();
    await act(async () => { finishStream(); await sending; });
    expect(mocks.session.ensureConversation).toHaveBeenCalledTimes(1);
    expect(mocks.session.markConversationStarted).toHaveBeenCalledWith("c2", "");
    expect(mocks.session.cancelConversationSubmission).not.toHaveBeenCalled();
    expect(result.current.state.messages[0]?.content).toBe("第一条消息");
  });

  it("removes the temporary sidebar row when the first send fails before acceptance", async () => {
    mocks.session.conversationId = "c2";
    mocks.session.projectId = "";
    mocks.session.draftConversationId = "c2";
    vi.mocked(sendMessageStream).mockRejectedValue(new Error("发送失败"));
    const { result } = renderHook(() => useChatStream({ scroll }));
    await act(async () => { await Promise.resolve(); });
    await act(() => result.current.sendMessage("第一条消息"));
    expect(mocks.session.markConversationSubmitted).toHaveBeenCalledWith("c2", "", "第一条消息");
    expect(mocks.session.cancelConversationSubmission).toHaveBeenCalledWith("c2");
    expect(mocks.session.markConversationStarted).not.toHaveBeenCalled();
  });

  it("removes the local row if creating the conversation fails", async () => {
    mocks.session.conversationId = null;
    mocks.session.projectId = "";
    mocks.session.ensureConversation = vi.fn().mockResolvedValue(null);
    const { result } = renderHook(() => useChatStream({ scroll }));
    await act(async () => { await Promise.resolve(); });
    await act(() => result.current.sendMessage("第一条消息"));
    const localId = vi.mocked(mocks.session.markConversationSubmitted).mock.calls[0]?.[0];
    expect(localId).toMatch(/^local:/);
    expect(mocks.session.cancelConversationSubmission).toHaveBeenCalledWith(localId);
    expect(sendMessageStream).not.toHaveBeenCalled();
  });

  it("sends the selected model ID and records it on the streaming reply", async () => {
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({
        type: "message_started",
        conversation_id: "c1",
        request_id: "r",
        user_message_id: "u1",
        message_id: "a1",
        model: {
          id: "custom:deepseek-flash",
          display_name: "DeepSeek Flash",
          provider: "deepseek",
          model: "deepseek-v4-flash",
        },
      });
      onEvent({ type: "completed", message_id: "a1", content: "你好", assistant_steps: [] });
      onEvent({ type: "done", terminal_reason: "completed" });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    await act(() => result.current.sendMessage("test", "tushare-fetcher"));
    expect(vi.mocked(sendMessageStream).mock.calls[0]?.[1]).toMatchObject({
      modelId: "custom:deepseek-flash",
      skillId: "tushare-fetcher",
    });
    expect(result.current.state.messages[1]).toMatchObject({
      content: "你好",
      model: { id: "custom:deepseek-flash", model: "deepseek-v4-flash" },
    });
  });

  it("re-syncs history only when the reply is a receipt without further execution", async () => {
    // 正常跑完的流自己会推进 UI，不需要额外拉取历史。
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({ type: "completed", message_id: "a1", content: "你好", assistant_steps: [] });
      onEvent({ type: "done", terminal_reason: "completed" });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    const baseline = vi.mocked(getConversationHistory).mock.calls.length;
    await act(() => result.current.sendMessage("test"));
    await waitFor(() => expect(mocks.session.busy).toBe(false));
    expect(vi.mocked(getConversationHistory).mock.calls.length).toBe(baseline);

    // 幂等重试的回执流不会继续执行：必须回到服务端对账，否则用户只看到卡片
    // 消失、然后什么都没发生。
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({ type: "user_input_accepted", interaction_id: "i1", assistant_message_id: "a2" });
      onEvent({ type: "done", terminal_reason: "already_accepted" });
    });
    await act(() => result.current.sendMessage("再提一次"));
    await waitFor(() =>
      expect(vi.mocked(getConversationHistory).mock.calls.length).toBeGreaterThan(baseline),
    );
  });

  it("explains an unresolved round instead of leaving it blank", async () => {
    // 答案已收但这一轮没跑完：历史里必须说清楚发生了什么、下一步怎么做，
    // 而不是留一条空消息让用户干等。
    vi.mocked(getConversationHistory).mockResolvedValue({
      ...emptyHistory,
      items: [
        {
          role: "assistant",
          id: "a1",
          status: "failed",
          error_code: "user_input_recovery_required",
          content: "",
          assistant_steps: [],
          display_metadata: {},
        },
      ],
      pending_interaction: null,
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.messages[0]?.content).toContain("发新消息"));
    expect(result.current.state.userQuestion).toBeNull();
  });

  it("turns a stale pending round into a notice and keeps the composer usable", async () => {
    // 服务端遗留的 pending 轮次（如 Web 进程重启）：不能设置全局 error——
    // ChatView 在 error 存在时禁用输入框，会把「发新消息解锁」这唯一的出口堵死。
    vi.mocked(getConversationHistory).mockResolvedValue({
      ...emptyHistory,
      items: [
        {
          role: "assistant",
          id: "a1",
          status: "pending",
          content: "",
          assistant_steps: [],
          display_metadata: {},
        },
      ],
      pending_interaction: null,
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.messages[0]?.content).toContain("发送新消息"));
    expect(result.current.state.error).toBeNull();
    expect(mocks.session.busy).toBe(false);
    expect(mocks.session.runStatus).toBeNull();
  });

  it("allows a new message to replace an expired question", async () => {
    mocks.session.busy = true;
    vi.mocked(getConversationHistory).mockResolvedValue({
      ...emptyHistory,
      pending_interaction: {
        kind: "user_question",
        schema_version: 2,
        interaction_id: "i-expired",
        interrupt_id: "interrupt-expired",
        assistant_message_id: "a-expired",
        questions: [
          {
            id: "q1",
            question: "已经过期的问题",
            options: [],
            allow_custom_answer: true,
            multi_select: false,
          },
        ],
        expires_at: "2020-01-01T00:00:00Z",
      },
    });
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({
        type: "message_started",
        conversation_id: "c1",
        request_id: "r-new",
        user_message_id: "u-new",
        message_id: "a-new",
      });
      onEvent({ type: "completed", message_id: "a-new", content: "新一轮", assistant_steps: [] });
      onEvent({ type: "done", terminal_reason: "completed" });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.userQuestion).not.toBeNull());
    await act(() => result.current.sendMessage("开始新一轮"));
    expect(sendMessageStream).toHaveBeenCalledOnce();
    expect(result.current.state.userQuestion).toBeNull();
  });

  it("ignores a late history response after sending", async () => {
    const history = deferred<ConversationHistory>();
    vi.mocked(getConversationHistory).mockReturnValue(history.promise);
    vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
      onEvent({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: "u1", message_id: "a1" });
      onEvent({ type: "text", text: "你好" });
      onEvent({ type: "approval_required", request: {
        approval_batch_id: "batch-approval",
        assistant_message_id: "a-new",
        interrupts: [{ id: "approval", actions: [] }],
      } });
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
    expect(mocks.session.runStatus).toBe("starting");
    act(() => emit({ type: "run_phase", phase: "selecting_tools" }));
    act(() => emit({ type: "text", text: '{"tools":' }));
    expect(mocks.session.runStatus).toBe("selecting_tools");
    act(() => emit({ type: "text", text: "[]}" }));
    expect(result.current.state.messages[1].content).toBe("");
    expect(mocks.session.runStatus).toBe("selecting_tools");
    act(() => emit({ type: "run_phase", phase: "thinking" }));
    expect(mocks.session.runStatus).toBe("thinking");
    expect(result.current.state.messages[1].content).toBe("");
    act(() => emit({ type: "text", text: "正文" }));
    expect(mocks.session.runStatus).toBe("responding");
    act(() => emit({ type: "tool_call", name: "example", args: {}, status: "started" }));
    expect(mocks.session.runStatus).toBe("processing");
    act(() => emit({ type: "completed", message_id: "a1", content: '{"tools":[]}', assistant_steps: [] }));
    await act(async () => { emit({ type: "done", terminal_reason: "completed" }); stream.resolve(); await task; });
    expect(result.current.state.messages[1].content).toBe("");
  });
  it("clears approval only after resume is accepted and can show a subsequent interrupt", async () => {
    const approval = {
      approval_batch_id: "batch-first",
      assistant_message_id: "a1",
      interrupts: [{ id: "first", actions: [] }],
    };
    vi.mocked(getConversationHistory).mockResolvedValue({ ...emptyHistory, pending_approval: approval, items: [{ id: "a1", role: "assistant", content: "", status: "interrupted", assistant_steps: [] }] });
    const stream = deferred<void>(); let emit!: (event: StreamEvent) => void;
    vi.mocked(sendApprovalStream).mockImplementation((_id, _input, { onEvent }) => { emit = onEvent; return stream.promise; });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.approval?.interrupts[0]?.id).toBe("first"));
    let task!: Promise<void>; act(() => { task = result.current.submitApproval([{ type: "reject" }]); });
    expect(result.current.state.approval?.interrupts[0]?.id).toBe("first");
    act(() => emit({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: null, message_id: "a1", resuming: true }));
    expect(result.current.state.approval).toBeNull(); expect(result.current.state.messages[0].status).toBe("streaming");
    await act(async () => { emit({ type: "approval_required", request: {
      approval_batch_id: "batch-second",
      assistant_message_id: "a1",
      interrupts: [{ id: "second", actions: [] }],
    } }); stream.resolve(); await task; });
    expect(result.current.state.approval?.interrupts[0]?.id).toBe("second");
    expect(vi.mocked(sendApprovalStream).mock.calls[0]?.[1]).toMatchObject({
      approvalBatchId: "batch-first",
      assistantMessageId: "a1",
    });
  });
  it("drops a redelivered SSE frame using the server frame id", async () => {
    const stream = deferred<void>();
    let emit!: (event: StreamEvent, eventId?: number) => void;
    vi.mocked(sendMessageStream).mockImplementation((_id, _input, { onEvent }) => {
      emit = onEvent;
      return stream.promise;
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let task!: Promise<void>;
    act(() => { task = result.current.sendMessage("test"); });
    await waitFor(() => expect(emit).toBeDefined());
    act(() => {
      emit({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: "u1", message_id: "a1" }, 1);
      emit({ type: "assistant_step_started", message_id: "a1", step: { id: "run:step:0", ordinal: 0, content: "", status: "streaming", is_final: false, tool_calls: [] } }, 2);
      emit({ type: "assistant_text_delta", message_id: "a1", step_id: "run:step:0", delta: "你好" }, 3);
      // 同一帧重复投递：序号没有增长，必须被丢弃。
      emit({ type: "assistant_text_delta", message_id: "a1", step_id: "run:step:0", delta: "你好" }, 3);
    });
    await waitFor(() => expect(result.current.state.messages[1].assistantSteps?.[0].content).toBe("你好"));
    expect(result.current.state.messages[1].assistantSteps).toHaveLength(1);
    act(() => emit({ type: "completed", message_id: "a1", content: "你好", assistant_steps: [] }, 4));
    act(() => emit({ type: "done", terminal_reason: "completed" }, 5));
    stream.resolve();
    await act(async () => { await task; });
  });

  it("flushes batched text before the next non-text event", async () => {
    const stream = deferred<void>();
    let emit!: (event: StreamEvent) => void;
    vi.mocked(sendMessageStream).mockImplementation((_id, _input, { onEvent }) => {
      emit = onEvent;
      return stream.promise;
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let task!: Promise<void>;
    act(() => { task = result.current.sendMessage("test"); });
    await waitFor(() => expect(emit).toBeDefined());
    act(() => {
      emit({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: "u1", message_id: "a1" });
      emit({ type: "assistant_step_started", message_id: "a1", step: { id: "run:step:0", ordinal: 0, content: "", status: "streaming", is_final: false, tool_calls: [] } });
      emit({ type: "assistant_text_delta", message_id: "a1", step_id: "run:step:0", delta: "你" });
      emit({ type: "assistant_text_delta", message_id: "a1", step_id: "run:step:0", delta: "好" });
      emit({ type: "run_phase", phase: "processing" });
    });
    expect(result.current.state.messages[1].assistantSteps[0].content).toBe("你好");
    await act(async () => {
      emit({ type: "completed", message_id: "a1", content: "你好", assistant_steps: [] });
      emit({ type: "done", terminal_reason: "completed" });
      stream.resolve();
      await task;
    });
  });

  it("does not mark an old completed answer as failed when a new request fails before starting", () => {
    const state = { ...INITIAL_CHAT_STATE, messages: [{ id: "old", role: "assistant" as const, content: "done", status: "completed" as const, markdown: true, events: [], assistantSteps: [], phases: [] }] };
    expect(reducer(state, { type: "streamFailed", message: "draft", error: "offline" }).messages[0].status).toBe("completed");
  });
  it("tracks the conversation's own project for attachment hydration", () => {
    // 历史 authoritative：附件作用域跟会话归属走，不跟侧栏当前项目走。
    const loaded = reducer(INITIAL_CHAT_STATE, {
      type: "historyLoaded", conversationId: "c1", title: "t", projectId: "p1",
      messages: [], approval: null, userQuestion: null,
    });
    expect(loaded.conversationProjectId).toBe("p1");
    // 乐观发送沿用发送时的作用域；切会话/重载时清零。
    const optimisticUser = { id: "u", role: "user" as const, content: "hi", status: null, markdown: false, events: [], assistantSteps: [], phases: [] };
    const optimisticAssistant = { id: "a", role: "assistant" as const, content: "", status: "streaming" as const, markdown: false, events: [], assistantSteps: [], phases: [] };
    const optimistic = reducer(loaded, { type: "optimistic", conversationId: "c1", projectId: "p1", user: optimisticUser, assistant: optimisticAssistant });
    expect(optimistic.conversationProjectId).toBe("p1");
    expect(reducer(optimistic, { type: "reset", conversationId: null }).conversationProjectId).toBeNull();
    expect(reducer(optimistic, { type: "historyLoading", conversationId: "c1" }).conversationProjectId).toBeNull();
  });
  it("an old stream cannot detach the new conversation's controller", async () => {
    const first = deferred<void>(); const second = deferred<void>();
    let emitFirst!: (event: StreamEvent) => void;
    vi.mocked(sendMessageStream).mockImplementationOnce((_id, _input, { onEvent }) => {
      emitFirst = onEvent;
      return first.promise;
    }).mockImplementationOnce(() => second.promise);
    const { result, rerender } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let firstTask!: Promise<void>; act(() => { firstTask = result.current.sendMessage("one"); });
    mocks.session = { ...mocks.session, conversationId: "c2", epoch: 2, selectedModelId: "custom:deepseek-pro" };
    rerender(); await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    // busy 跟随当前视图：旧会话在后台跑不影响新会话的发送态。
    expect(mocks.session.busy).toBe(false);
    expect(result.current.runningConversationIds).toEqual(["c1"]);
    // 多会话并发：切走后新会话仍可发送，不被旧会话阻塞。
    let secondTask!: Promise<void>; act(() => { secondTask = result.current.sendMessage("two"); });
    expect(vi.mocked(sendMessageStream).mock.calls[0]?.[1].modelId).toBe("custom:deepseek-flash");
    expect(vi.mocked(sendMessageStream).mock.calls[1]?.[1].modelId).toBe("custom:deepseek-pro");
    // 旧会话的终态事件只写旧会话缓存，不影响当前视图与新会话的流。
    act(() => emitFirst({ type: "completed", message_id: "old", content: "old answer", assistant_steps: [] }));
    expect(mocks.session.busy).toBe(true);
    expect(result.current.state.messages.map((item) => item.content)).toEqual(["two", ""]);
    expect([...mocks.session.runningConversationIds]).toEqual(["c2"]);
    await act(async () => { first.resolve(); await firstTask; });
    // 旧流结束只注销自己：新会话仍在跑，busy 保持。
    expect(mocks.session.busy).toBe(true);
    expect([...mocks.session.runningConversationIds]).toEqual(["c2"]);
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
  it("stops the visible run explicitly and marks it cancelled", async () => {
    let emit!: (event: StreamEvent) => void;
    let streamSignal!: AbortSignal;
    let rejectStream!: (reason?: unknown) => void;
    vi.mocked(sendMessageStream).mockImplementation((_id, _input, { onEvent, signal }) => {
      emit = onEvent;
      streamSignal = signal!;
      return new Promise<void>((_resolve, reject) => {
        rejectStream = reject;
        signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    });
    const { result } = renderHook(() => useChatStream({ scroll }));
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    let task!: Promise<void>;
    act(() => { task = result.current.sendMessage("test"); });
    await waitFor(() => expect(streamSignal).toBeDefined());
    act(() => {
      emit({ type: "message_started", conversation_id: "c1", request_id: "r", user_message_id: "u1", message_id: "a1" });
    });
    expect(result.current.isRunning).toBe(true);
    let stopped!: boolean;
    act(() => { stopped = result.current.stopCurrent(); });
    expect(stopped).toBe(true);
    expect(streamSignal.aborted).toBe(true);
    // 显式取消立即本地收尾，不等后端对账。
    expect(result.current.state.messages.at(-1)).toMatchObject({ status: "cancelled" });
    expect(result.current.isRunning).toBe(false);
    expect(mocks.session.busy).toBe(false);
    await act(async () => {
      rejectStream(new DOMException("aborted", "AbortError"));
      await task;
    });
    // abort 后的流静默结束，不把 cancelled 覆盖成 failed。
    expect(result.current.state.messages.at(-1)).toMatchObject({ status: "cancelled" });
  });
  it("keeps the conversation stream alive while another conversation is selected", async () => {
    const stream = deferred<void>(); let streamSignal!: AbortSignal; let emit!: (event: StreamEvent) => void;
    let historyCalls = 0;
    vi.mocked(getConversationHistory).mockImplementation(async () => {
      historyCalls += 1;
      return historyCalls >= 3
        ? { ...emptyHistory, items: [{ id: "a1", role: "assistant", content: "", status: "pending", assistant_steps: [] }] }
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
    await waitFor(() => expect(mocks.session.busy).toBe(false));
    act(() => emit({ type: "approval_required", request: {
      approval_batch_id: "batch-background",
      assistant_message_id: "a1",
      interrupts: [{ id: "background", actions: [] }],
    } }));
    expect(mocks.session.busy).toBe(false);
    act(() => emit({ type: "run_phase", phase: "thinking" }));
    expect(mocks.session.runStatus).toBeNull();
    expect(result.current.state.approval).toBeNull();
    act(() => {
      mocks.session = { ...mocks.session, conversationId: "c1", epoch: 3 };
      rerender();
    });
    await waitFor(() => expect(result.current.state.historyLoading).toBe(false));
    act(() => emit({ type: "text", text: "继续输出" }));
    expect(result.current.state.messages.at(-1)?.content).toBe("继续输出");
    act(() => emit({ type: "completed", message_id: "a1", content: "继续输出", assistant_steps: [] }));
    act(() => emit({ type: "done", terminal_reason: "completed" }));
    stream.resolve();
    await act(async () => { await task; });
  });
});

it("refreshes the current user's skills after the install tool returns", async () => {
  mocks.session.refreshSkills = vi.fn().mockResolvedValue(undefined);
  vi.mocked(sendMessageStream).mockImplementation(async (_id, _input, { onEvent }) => {
    onEvent({ type: "message_started", conversation_id: "c1", request_id: "r1", user_message_id: "u1", message_id: "a1" });
    onEvent({ type: "assistant_tool_result", message_id: "a1", step_id: "step", call_id: "install", result: {
      call_id: "install", name: "confirm_skill_install", batch_index: 0, status: "completed",
      result_preview: '{"status":"installed"}',
    } });
    onEvent({ type: "completed", message_id: "a1", content: "安装成功", assistant_steps: [] });
    onEvent({ type: "done", terminal_reason: "completed" });
  });
  const { result } = renderHook(() => useChatStream({ scroll }));
  await act(async () => { await Promise.resolve(); });
  await act(() => result.current.sendMessage("安装 Skill"));
  expect(mocks.session.refreshSkills).toHaveBeenCalledTimes(1);
});
