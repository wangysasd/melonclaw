import { App as AntdApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarContent } from "../src/components/Sidebar";
import type { SessionContextValue } from "../src/state/session";

const session = {
  contextReady: true,
  status: { status: "ready" },
  projectId: "p1",
  conversationId: "c1",
  projects: [{ id: "p1", name: "项目甲", is_pinned: false }],
  conversations: [{ id: "c1", project_id: "p1", title: "对话甲", is_pinned: false }],
  conversationsLoading: false,
  conversationsLoadFailed: false,
  recents: [],
  conversationCursor: null,
  recentsCursor: null,
  runningConversationIds: [],
  openProject: vi.fn(),
  selectConversation: vi.fn(),
  openRecent: vi.fn(),
  newConversation: vi.fn(),
  updateProject: vi.fn().mockResolvedValue(true),
  updateConversation: vi.fn().mockResolvedValue(true),
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
  session.projectId = "p1";
  session.conversationsLoading = false;
  session.conversationsLoadFailed = false;
});

describe("sidebar resource actions", () => {
  it("creates a conversation from the icon after the project menu", () => {
    setup();
    const menu = screen.getByRole("button", { name: "项目「项目甲」更多操作" });
    const create = screen.getByRole("button", { name: "在「项目甲」中新建会话" });
    expect(menu.nextElementSibling).toBe(create);
    fireEvent.click(create);
    expect(session.newConversation).toHaveBeenCalledWith("p1");
  });

  it("shows project conversations under an open folder and toggles the project", () => {
    const view = setup();
    expect(screen.getByLabelText("项目甲的对话")).toBeTruthy();
    expect(screen.getByText("对话甲")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "在此项目中新建对话" })).toBeNull();
    expect(screen.getByRole("button", { name: "项目甲" }).querySelector('[style*="folder-open.svg"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "项目甲" }));
    expect(session.openProject).toHaveBeenCalledWith("p1");
    session.projectId = "";
    view.rerender(
      <AntdApp>
        <SidebarContent collapsed={false} onToggleCollapse={vi.fn()} onNewConversation={vi.fn()} onOpenProjectDialog={vi.fn()} />
      </AntdApp>,
    );
    expect(screen.queryByLabelText("项目甲的对话")).toBeNull();
    expect(screen.getByRole("button", { name: "项目甲" }).querySelector('[style*="folder.svg"]')).toBeTruthy();
  });

  it("shows the project create button only when the project has no conversations", () => {
    const existing = session.conversations;
    session.conversations = [];
    try {
      setup();
      fireEvent.click(screen.getByRole("button", { name: "在此项目中新建对话" }));
      expect(session.newConversation).toHaveBeenCalledWith("p1");
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
      expect(screen.queryByRole("button", { name: "在此项目中新建对话" })).toBeNull();
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
