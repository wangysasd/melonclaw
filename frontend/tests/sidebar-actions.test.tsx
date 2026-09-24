import { App as AntdApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarContent } from "../src/components/Sidebar";
import { listConversations } from "../src/api/client";
import type { SessionContextValue } from "../src/state/session";

vi.mock("../src/api/client", () => ({ listConversations: vi.fn() }));

const session = {
  contextReady: true,
  status: { status: "ready" },
  projectId: "p1",
  conversationId: "c1",
  conversationListRevision: 0,
  projects: [{ id: "p1", name: "项目甲", is_pinned: false }],
  conversations: [{ id: "c1", project_id: "p1", title: "对话甲", is_pinned: false }],
  conversationsLoading: false,
  conversationsLoadFailed: false,
  recents: [],
  conversationCursor: null,
  recentsCursor: null,
  runningConversationIds: [],
  optimisticConversations: [],
  acknowledgeConversationRows: vi.fn(),
  openProject: vi.fn(),
  openProjectConversation: vi.fn(),
  selectConversation: vi.fn(),
  openRecent: vi.fn(),
  startNewConversation: vi.fn(),
  updateProject: vi.fn().mockResolvedValue(true),
  updateConversation: vi.fn().mockResolvedValue(true),
  moveConversationToProject: vi.fn().mockResolvedValue(true),
  deleteProject: vi.fn().mockResolvedValue(true),
  deleteConversation: vi.fn().mockResolvedValue(true),
};

vi.mock("../src/state/session", () => ({ useSession: () => session as unknown as SessionContextValue }));
vi.mock("../src/components/UserPicker", () => ({ UserPicker: () => null }));
vi.mock("../src/hooks/useServiceStatus", () => ({ useServiceStatus: () => ({ status: null }) }));

function setup() {
  return render(
    <AntdApp>
      <SidebarContent collapsed={false} onToggleCollapse={vi.fn()} onNewConversation={vi.fn()} onOpenProjectDialog={vi.fn()} />
    </AntdApp>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listConversations).mockResolvedValue({ items: [], next_cursor: null });
  session.updateConversation.mockResolvedValue(true);
  session.projectId = "p1";
  session.conversationId = "c1";
  session.conversations = [{ id: "c1", project_id: "p1", title: "对话甲", is_pinned: false }];
  session.conversationListRevision = 0;
  session.projects = [{ id: "p1", name: "项目甲", is_pinned: false }];
  session.conversationsLoading = false;
  session.conversationsLoadFailed = false;
  session.optimisticConversations = [];
  session.recents = [];
  session.moveConversationToProject.mockResolvedValue(true);
});

describe("sidebar resource actions", () => {
  it("creates a conversation from the icon after the project menu", () => {
    setup();
    const menu = screen.getByRole("button", { name: "项目「项目甲」更多操作" });
    const create = screen.getByRole("button", { name: "在「项目甲」中新建会话" });
    expect(menu.nextElementSibling).toBe(create);
    fireEvent.click(create);
    expect(session.startNewConversation).toHaveBeenCalledWith("p1");
  });

  it("shows a submitted conversation immediately while the server list is still empty", () => {
    session.conversations = [];
    session.optimisticConversations = [{ id: "c2", project_id: "p1", title: "刚发送的消息", updated_at: "2026-09-23T05:00:00Z" }];
    setup();
    expect(screen.getByText("刚发送的消息")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "在此项目中新建首个对话" })).toBeNull();
    expect(screen.queryByRole("button", { name: "对话「刚发送的消息」更多操作" })).toBeNull();
  });

  it("shows but disables a local row before the server returns its conversation ID", () => {
    session.conversations = [];
    session.optimisticConversations = [{ id: "local:1", project_id: "p1", title: "第一条消息", localOnly: true }];
    setup();
    expect(screen.getByRole("button", { name: /第一条消息/ }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("button", { name: "在此项目中新建首个对话" })).toBeNull();
  });

  it("toggles the folder without changing the active chat", () => {
    setup();
    expect(screen.getByLabelText("项目甲的对话")).toBeTruthy();
    expect(screen.getByText("对话甲")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "在此项目中新建首个对话" })).toBeNull();
    expect(screen.getByRole("button", { name: "项目甲" }).querySelector('[style*="folder-open.svg"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "项目甲" }));
    expect(session.openProject).not.toHaveBeenCalled();
    expect(session.selectConversation).not.toHaveBeenCalled();
    expect(session.projectId).toBe("p1");
    expect(session.conversationId).toBe("c1");
    expect(screen.queryByLabelText("项目甲的对话")).toBeNull();
    expect(screen.getByRole("button", { name: "项目甲" }).querySelector('[style*="folder.svg"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "项目甲" }));
    expect(screen.getByLabelText("项目甲的对话")).toBeTruthy();
  });

  it("opens a conversation from another folder only when its conversation row is clicked", async () => {
    session.projects = [...session.projects, { id: "p2", name: "项目乙", is_pinned: false }];
    vi.mocked(listConversations).mockResolvedValue({ items: [{ id: "c2", project_id: "p2", title: "对话乙", is_pinned: false, updated_at: "2026-09-23T05:00:00Z" }], next_cursor: null });
    setup();
    fireEvent.click(screen.getByRole("button", { name: "项目乙" }));
    expect(session.openProject).not.toHaveBeenCalled();
    expect(session.conversationId).toBe("c1");
    fireEvent.click(await screen.findByTitle("对话乙"));
    expect(session.openProjectConversation).toHaveBeenCalledWith("p2", "c2");
  });

  it("refreshes an expanded other project when a first message starts", async () => {
    session.projects = [...session.projects, { id: "p2", name: "项目乙", is_pinned: false }];
    vi.mocked(listConversations).mockImplementation(async () => ({
      items: session.conversationListRevision ? [{ id: "c2", project_id: "p2", title: "首条消息", is_pinned: false, updated_at: "2026-09-23T05:00:00Z" }] : [],
      next_cursor: null,
    }));
    const view = setup();
    fireEvent.click(screen.getByRole("button", { name: "项目乙" }));
    await waitFor(() => expect(listConversations).toHaveBeenCalledTimes(1));
    session.conversationListRevision = 1;
    view.rerender(
      <AntdApp>
        <SidebarContent collapsed={false} onToggleCollapse={vi.fn()} onNewConversation={vi.fn()} onOpenProjectDialog={vi.fn()} />
      </AntdApp>,
    );
    expect(await screen.findByText("首条消息")).toBeTruthy();
    expect(listConversations).toHaveBeenCalledTimes(2);
  });

  it("shows the project create button only when the project has no conversations", () => {
    const existing = session.conversations;
    session.conversations = [];
    try {
      setup();
      fireEvent.click(screen.getByRole("button", { name: "在此项目中新建首个对话" }));
      expect(session.startNewConversation).toHaveBeenCalledWith("p1");
    } finally {
      session.conversations = existing;
    }
  });

  it("does not show the create button while project conversations are loading", () => {
    const existing = session.conversations;
    session.conversations = [];
    session.conversationsLoading = true;
    try {
      setup();
      expect(screen.queryByRole("button", { name: "在此项目中新建首个对话" })).toBeNull();
      expect(screen.getByText("会话加载中…")).toBeTruthy();
    } finally {
      session.conversations = existing;
    }
  });

  it("pins and renames a conversation from its menu", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "对话「对话甲」更多操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "置顶" }));
    await waitFor(() => expect(session.updateConversation).toHaveBeenCalledWith("c1", { isPinned: true }));
    fireEvent.click(screen.getByRole("button", { name: "对话「对话甲」更多操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "重命名" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新名称" }), { target: { value: "新名称" } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
    await waitFor(() => expect(session.updateConversation).toHaveBeenCalledWith("c1", { name: "新名称" }));
  });

  it("offers a project submenu only for ordinary conversations", async () => {
    session.recents = [{ id: "c0", project_id: null, title: "普通对话", is_pinned: false }];
    const onOpenProjectDialog = vi.fn();
    render(
      <AntdApp>
        <SidebarContent collapsed={false} onToggleCollapse={vi.fn()} onNewConversation={vi.fn()} onOpenProjectDialog={onOpenProjectDialog} />
      </AntdApp>,
    );
    fireEvent.click(screen.getByRole("button", { name: "对话「对话甲」更多操作" }));
    expect(screen.queryByRole("menuitem", { name: "移动到项目" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "对话「对话甲」更多操作" }));
    fireEvent.click(screen.getByRole("button", { name: "对话「普通对话」更多操作" }));
    const move = await screen.findByText("移动到项目");
    fireEvent.mouseEnter(move);
    fireEvent.click(await screen.findByRole("menuitem", { name: "项目甲" }));
    await waitFor(() => expect(session.moveConversationToProject).toHaveBeenCalledWith("c0", "p1"));
  });

  it("opens project creation with the ordinary conversation as its move target", async () => {
    session.recents = [{ id: "c0", project_id: null, title: "普通对话", is_pinned: false }];
    const onOpenProjectDialog = vi.fn();
    render(
      <AntdApp>
        <SidebarContent collapsed={false} onToggleCollapse={vi.fn()} onNewConversation={vi.fn()} onOpenProjectDialog={onOpenProjectDialog} />
      </AntdApp>,
    );
    fireEvent.click(screen.getByRole("button", { name: "对话「普通对话」更多操作" }));
    fireEvent.mouseEnter(await screen.findByText("移动到项目"));
    fireEvent.click(await screen.findByRole("menuitem", { name: "新建项目" }));
    expect(onOpenProjectDialog).toHaveBeenCalledWith("c0");
  });

  it("confirms deletion of a project before calling the service", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "项目「项目甲」更多操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "删除" }));
    expect(session.deleteProject).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog", { name: /删除项目/ });
    fireEvent.click(dialog.querySelector(".ant-btn-primary") as HTMLElement);
    await waitFor(() => expect(session.deleteProject).toHaveBeenCalledWith("p1"));
  });
});
