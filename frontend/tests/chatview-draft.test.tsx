import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../src/hooks/useChatStream";
import { ChatView } from "../src/components/ChatView";
import * as client from "../src/api/client";
import type { ConversationSummary, Project } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  attachmentContentUrl: (id: string) => `/api/attachments/${id}/content`,
  deleteAttachment: vi.fn(),
  getAttachment: vi.fn(),
  getAttachmentCapabilities: vi.fn(),
  getConversationHistory: vi.fn(),
  retryAttachmentParse: vi.fn(),
  uploadAttachment: vi.fn(),
}));

vi.mock("../src/api/results", () => ({
  conversationArtifacts: vi.fn(async () => ({ items: [] })),
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
        notification: {},
        modal: {},
      }),
    },
  };
});

const session = {
  users: [],
  userId: "u1",
  projectId: "",
  conversationId: "c1",
  contextReady: true,
  status: { status: "ready" },
  busy: false,
  runStatus: null as string | null,
  conversationCreating: false,
  projects: [] as Project[],
  conversations: [] as ConversationSummary[],
  recents: [] as ConversationSummary[],
  optimisticConversations: [] as ConversationSummary[],
  skills: [],
  skillsLoading: false,
  skillsError: null,
  modelOptions: [],
  selectedModelId: "",
  selectModel: vi.fn(),
  runningConversationIds: [],
};
vi.mock("../src/state/session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/state/session")>();
  return { ...actual, useSession: () => session };
});

const chatState = {
  conversationId: "c1" as string | null,
  conversationTitle: "新会话",
  conversationProjectId: null as string | null,
  messages: [] as ChatMessage[],
  approval: null,
  userQuestion: null,
  historyLoading: false,
  restoreDraft: null,
  error: null,
};
const chatStub = {
  state: chatState,
  isRunning: false,
  runningConversationIds: [],
  stopCurrent: vi.fn(),
  sendMessage: vi.fn(),
  submitApproval: vi.fn(),
  submitUserInput: vi.fn(),
  clearRestoreDraft: vi.fn(),
  reloadHistory: vi.fn(),
  loadMessage: vi.fn(),
};
vi.mock("../src/hooks/useChatStream", () => ({ useChatStream: () => chatStub }));

async function flush(times = 3): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await act(async () => { await Promise.resolve(); });
  }
}

// jsdom 的 div 没有 scrollTo：聊天视图切会话会滚到底，这里打桩。
if (typeof Element.prototype.scrollTo !== "function") {
  Element.prototype.scrollTo = function () { /* noop */ };
}

beforeEach(() => {
  session.conversationId = "c1";
  session.projectId = "";
  session.projects = [];
  session.conversations = [];
  session.recents = [];
  chatState.conversationId = "c1";
  chatState.conversationTitle = "新会话";
  chatState.conversationProjectId = null;
  chatState.restoreDraft = null;
  chatState.messages = [];
  vi.mocked(client.getAttachmentCapabilities).mockReset();
  vi.mocked(client.getAttachmentCapabilities).mockResolvedValue({
    items: [{ extension: ".txt", media_type: "text/plain", kind: "text" }],
    max_file_bytes: 10, max_total_bytes: 25, max_per_message: 2,
    workspace_max_bytes: 100, image_max_pixels: 100, pdf_max_pages: 1,
  });
  vi.clearAllMocks();
});

describe("chatview draft", () => {
  it("shows the project name before a project conversation title", () => {
    session.projectId = "p1";
    session.projects = [{ id: "p1", name: "第一个项目" }];
    session.conversations = [{ id: "c1", project_id: "p1", title: "新会话" }];
    chatState.conversationProjectId = "p1";
    const view = render(<ChatView />);
    expect(view.container.querySelector(".topbar-session-name")?.textContent).toBe("第一个项目/新会话");

    session.projects = [{ id: "p1", name: "已重命名项目" }];
    view.rerender(<ChatView />);
    expect(view.container.querySelector(".topbar-session-name")?.textContent).toBe("已重命名项目/新会话");
  });

  it("keeps ordinary conversation titles without a project prefix", () => {
    session.projectId = "p1";
    session.projects = [{ id: "p1", name: "第一个项目" }];
    session.conversations = [{ id: "c1", project_id: null, title: "新会话" }];
    const view = render(<ChatView />);
    expect(view.container.querySelector(".topbar-session-name")?.textContent).toBe("新会话");
  });

  it("切会话时清空旧草稿，新会话落到一张白纸", async () => {
    const view = render(<ChatView />);
    await flush();
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "旧会话没发出去的话" } });
    expect(input.value).toBe("旧会话没发出去的话");

    // 切到刚建好的新会话：草稿必须清空，否则看起来和没点新建一样。
    session.conversationId = "c2";
    chatState.conversationId = "c2";
    view.rerender(<ChatView />);
    await flush();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
    expect(chatStub.clearRestoreDraft).toHaveBeenCalled();
    view.unmount();
  });
});


describe("long-running chat feedback", () => {
  const assistantMessage = (): ChatMessage => ({ id: "a1", role: "assistant", content: "", status: "streaming", markdown: false, events: [], assistantSteps: [], phases: ["responding"] });
  it("shows streaming text and reasoning in the message, then reconciles tool and final output", () => {
    const usageEvents = [
      { type: "model_usage", call_id: "main", kind: "main", status: "started", input_tokens: null, output_tokens: null },
      { type: "model_usage", call_id: "main", kind: "main", status: "completed", input_tokens: 31429, output_tokens: 2023 },
      { type: "model_usage", call_id: "main", kind: "main", status: "completed", input_tokens: 31429, output_tokens: 2023 },
      { type: "model_usage", call_id: "selector", kind: "selection", status: "completed", input_tokens: 10, output_tokens: 4 },
      { type: "context_usage", scope: "main", estimated_input_tokens: 14449, context_window: 1000000, summary_trigger_tokens: 800000 },
    ];
    const step = { id: "s1", ordinal: 0, content: "先分析临时回答", status: "streaming" as const, is_final: false, tool_calls: [], content_blocks: [
      { type: "reasoning" as const, text: "先分析" }, { type: "text" as const, text: "临时回答" },
    ] };
    chatState.messages = [{ ...assistantMessage(), events: usageEvents, assistantSteps: [step] }];
    const view = render(<ChatView />);
    expect(view.container.querySelector(".message-body")?.textContent).toContain("临时回答");
    expect(view.container.querySelector(".assistant-reasoning")?.textContent).toContain("先分析");
    expect(view.container.querySelector(".agent-execution-body .assistant-reasoning")).not.toBeNull();
    expect(view.container.querySelector(".message-token-usage")).toBeNull();
    expect(screen.queryByText(/模型用量|最近一次主模型上下文/)).toBeNull();

    const toolStep = { ...step, tool_calls: [{ call_id: "c1", name: "read_file", batch_index: 0, status: "running" as const }] };
    chatState.messages = [{ ...assistantMessage(), events: usageEvents, assistantSteps: [toolStep] }];
    view.rerender(<ChatView />);
    expect(view.container.querySelector(".message-body")?.textContent).not.toContain("临时回答");
    expect(view.container.querySelector(".agent-execution-body")?.textContent).toContain("临时回答");

    chatState.messages = [{ ...assistantMessage(), events: usageEvents, status: "completed", content: "最终答复", assistantSteps: [
      { ...toolStep, status: "completed", tool_calls: [{ ...toolStep.tool_calls[0], status: "completed" as const }] },
      { id: "s2", ordinal: 1, content: "最终答复", status: "completed", is_final: true, tool_calls: [] },
    ] }];
    view.rerender(<ChatView />);
    expect(view.container.querySelector(".message-body")?.textContent).toContain("最终答复");
    expect(view.container.querySelector(".assistant-reasoning summary")?.textContent).toBe("思考过程");
    const execution = view.container.querySelector(".agent-execution")!;
    expect(execution.children[0].className).toBe("agent-execution-head");
    expect(execution.children[0].textContent).toContain("完成");
    expect(execution.children[1].className).toBe("agent-execution-body");
    expect(execution.querySelector(".agent-timeline")?.firstElementChild?.className).toBe("assistant-reasoning");
    expect(execution.querySelector(".agent-tool summary")?.textContent).toContain("读取文件");
    expect(execution.querySelector(".agent-execution-body")).not.toBeNull();
    expect(execution.textContent).not.toContain("查看耗时");
    expect(execution.textContent).not.toContain("最终答复");
    const usage = screen.getByLabelText("输入输出 token 数");
    expect(usage.textContent).toBe("输入 31439 | 输出 2027");
    expect(execution.contains(usage)).toBe(false);
    expect(view.container.querySelector(".message-body")!.compareDocumentPosition(usage) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(usage.compareDocumentPosition(view.container.querySelector(".message-actions")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText(/模型用量|最近一次主模型上下文/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /查看执行步骤，完成/ }));
    expect(execution.querySelector(".agent-execution-body")).toBeNull();
    expect(execution.querySelector(".assistant-reasoning")).toBeNull();
    expect(view.container.querySelector(".message-body")?.textContent).toContain("最终答复");
    view.unmount();
  });
  it("marks new text while browsing history and keeps the scroll position until clicked", async () => {
    chatState.messages = [assistantMessage()];
    const view = render(<ChatView />);
    const conversation = screen.getByLabelText("聊天记录");
    Object.defineProperties(conversation, { scrollHeight: { value: 1000, configurable: true }, clientHeight: { value: 200, configurable: true } });
    conversation.scrollTop = 10;
    fireEvent.scroll(conversation);
    expect(screen.getByRole("button", { name: "回到最新消息" })).toBeTruthy();
    const scrollTo = vi.fn(); conversation.scrollTo = scrollTo;
    chatState.messages = [{ ...chatState.messages[0], content: "收到新内容" }];
    view.rerender(<ChatView />);
    const latest = screen.getByRole("button", { name: "有新内容 · 回到最新消息" });
    expect(conversation.scrollTop).toBe(10);
    expect(scrollTo).not.toHaveBeenCalled();
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
    fireEvent.click(latest);
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "auto" });
    expect(screen.queryByRole("button", { name: "有新内容 · 回到最新消息" })).toBeNull();
    view.unmount(); vi.unstubAllGlobals();
  });
  it("shows stopped content and uncertain operations with a sync action", () => {
    chatState.messages = [{ ...assistantMessage(), status: "cancelled", content: "保留下来的回答", assistantSteps: [{
      id: "s", ordinal: 0, content: "过程说明", status: "running", is_final: false,
      tool_calls: [{ call_id: "c", name: "execute", batch_index: 0, status: "running", args_preview: '{"command":"job"}' }],
    }] }];
    const view = render(<ChatView />);
    expect(screen.getByText(/已保留：已收到的回答、过程文本/)).toBeTruthy();
    expect(screen.getByText("执行命令 · job")).toBeTruthy();
    expect(view.container.querySelector(".agent-tool.is-unknown")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "重新同步会话，核对结果" }));
    expect(chatStub.reloadHistory).toHaveBeenCalled();
    view.unmount();
  });
});


it("disables historical recovery buttons while the current conversation is running", () => {
  chatState.messages = [
    { id: "failed", role: "assistant", content: "", status: "failed", markdown: true, phases: [], events: [], assistantSteps: [] },
    { id: "stopped", role: "assistant", content: "", status: "cancelled", markdown: true, phases: [], events: [], assistantSteps: [
      { id: "s1", ordinal: 0, content: "", status: "running", is_final: false, tool_calls: [{ call_id: "c", name: "execute", batch_index: 0, status: "running" }] },
    ] },
  ];
  chatStub.isRunning = true;
  const { rerender } = render(<ChatView />);
  const buttons = screen.getAllByRole<HTMLButtonElement>("button", { name: "重新同步会话，核对结果" });
  expect(buttons).toHaveLength(2);
  for (const button of buttons) {
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
  }
  expect(chatStub.reloadHistory).not.toHaveBeenCalled();
  chatStub.isRunning = false;
  rerender(<ChatView />);
  for (const button of screen.getAllByRole<HTMLButtonElement>("button", { name: "重新同步会话，核对结果" })) expect(button.disabled).toBe(false);
});
