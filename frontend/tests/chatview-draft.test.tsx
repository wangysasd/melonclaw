import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  tenantId: "t1",
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
  messages: [],
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
