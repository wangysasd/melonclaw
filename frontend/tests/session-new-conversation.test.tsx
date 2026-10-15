import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession, type SessionContextValue } from "../src/state/session";
import * as client from "../src/api/client";
import { projectStorageKey } from "../src/state/storage";
import type { ConversationSummary } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  createConversation: vi.fn(),
  createProject: vi.fn(),
  getStatus: vi.fn(),
  listConversations: vi.fn(),
  listDevUsers: vi.fn(),
  listModels: vi.fn(),
  listProjects: vi.fn(),
  listSkills: vi.fn(),
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

function conversation(id: string): ConversationSummary {
  return { id, project_id: null, title: "新会话" };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

let session: SessionContextValue | null = null;
function Probe() {
  session = useSession();
  return null;
}

async function renderBootstrapped() {
  session = null;
  const view = render(
    <SessionProvider initialUserId="u1">
      <Probe />
    </SessionProvider>,
  );
  await waitFor(() => expect(session?.contextReady).toBe(true), { timeout: 5000 });
  if (!session) throw new Error("session 尚未就绪");
  return view;
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(client.getStatus).mockReset();
  vi.mocked(client.getStatus).mockResolvedValue({
    status: "ready", message: "", provider: "deepseek", model: "deepseek-flash",
    mcp_servers: [], skills: [], database: "connected", memory_store: "connected",
  });
  vi.mocked(client.listDevUsers).mockReset();
  vi.mocked(client.listDevUsers).mockResolvedValue({
    items: [{
      user_id: "u1", display_name: "U1", user_name_zh: "U1", username: "u1",
      is_default: true, tenant_id: "t1", tenant_role: "member", tenant_status: "active",
    }],
  });
  vi.mocked(client.listModels).mockReset();
  vi.mocked(client.listModels).mockResolvedValue({ items: [], default_model_id: "" });
  vi.mocked(client.listProjects).mockReset();
  vi.mocked(client.listProjects).mockResolvedValue({ items: [] });
  vi.mocked(client.listSkills).mockReset();
  vi.mocked(client.listSkills).mockResolvedValue({ items: [] });
  vi.mocked(client.listConversations).mockReset();
  vi.mocked(client.listConversations).mockResolvedValue({ items: [], next_cursor: null });
  vi.mocked(client.createConversation).mockReset();
});

describe("project conversation navigation", () => {
  it("ignores a previous provider's late skill response after switching identity", async () => {
    const oldSkills = deferred<{ items: { id: string; display_name: string; description: string }[] }>();
    const view = await renderBootstrapped();
    vi.mocked(client.listSkills).mockImplementationOnce(() => oldSkills.promise);
    let loading!: Promise<void>;
    act(() => { loading = session!.refreshSkills(); });
    view.unmount();
    const next = await renderBootstrapped();
    await act(async () => {
      oldSkills.resolve({ items: [{ id: "old", display_name: "Old", description: "" }] });
      await loading;
    });
    expect(session?.skills).toEqual([]);
    next.unmount();
  });

  it("selects a conversation in another project without an intermediate empty chat", async () => {
    localStorage.setItem(projectStorageKey("u1"), "p1");
    vi.mocked(client.listProjects).mockResolvedValue({ items: [
      { id: "p1", name: "项目甲", is_pinned: false },
      { id: "p2", name: "项目乙", is_pinned: false },
    ] });
    vi.mocked(client.listConversations).mockImplementation(async (input) => ({
      items: input.projectId === "p1" ? [{ ...conversation("c1"), project_id: "p1" }] : input.projectId === "p2" ? [{ ...conversation("c2"), project_id: "p2" }] : [],
      next_cursor: null,
    }));
    const view = await renderBootstrapped();
    await waitFor(() => expect(session?.conversationId).toBe("c1"));

    act(() => { session!.openProjectConversation("p2", "c2"); });
    expect(session?.projectId).toBe("p2");
    expect(session?.conversationId).toBe("c2");
    await waitFor(() => expect(session?.conversations[0]?.id).toBe("c2"));
    view.unmount();
  });
});

describe("task conversation refresh", () => {
  it.each(["loadMoreConversations", "loadMoreRecents"] as const)(
    "deduplicates refreshed tasks when %s reads an overlapping page",
    async (loadMore) => {
      const pinned = { ...conversation("pinned"), is_pinned: true, updated_at: "2026-10-15T00:00:00Z" };
      const existing = { ...conversation("existing"), task_id: "task-1", updated_at: "2026-10-15T02:00:00Z" };
      const refreshed = { ...existing, title: "更新后的任务", updated_at: "2026-10-15T04:00:00Z" };
      const inserted = { ...conversation("inserted"), task_id: "task-2", updated_at: "2026-10-15T03:00:00Z" };
      const older = { ...conversation("older"), updated_at: "2026-10-15T01:00:00Z" };
      vi.mocked(client.listConversations).mockImplementation(async (input) => {
        if (input.taskOnly) return { items: [refreshed, inserted], next_cursor: "task-page-2" };
        if (input.cursor === "page-2") return { items: [inserted, older], next_cursor: "page-3" };
        return { items: [pinned, existing], next_cursor: "page-2" };
      });
      const view = await renderBootstrapped();
      const signal = new AbortController().signal;
      await act(async () => { await session!.refreshTaskConversations(signal); });
      expect(client.listConversations).toHaveBeenLastCalledWith({ userId: "u1", taskOnly: true, limit: 100 }, signal);
      expect(session?.recents.map(item => item.id)).toEqual(["pinned", "existing", "inserted"]);
      expect(session?.recents.find(item => item.id === "existing")?.title).toBe("更新后的任务");
      expect(session?.recentsCursor).toBe("page-2");
      expect(session?.conversationCursor).toBe("page-2");

      await act(async () => { await session![loadMore](); });
      expect(session?.recents.map(item => item.id)).toEqual(["pinned", "existing", "inserted", "older"]);
      expect(session?.conversations).toEqual(session?.recents);
      expect(session?.recentsCursor).toBe("page-3");
      expect(session?.conversationCursor).toBe("page-3");
      view.unmount();
    },
  );

  it("updates recents without adding task conversations to the current project", async () => {
    localStorage.setItem(projectStorageKey("u1"), "p1");
    vi.mocked(client.listProjects).mockResolvedValue({ items: [{ id: "p1", name: "项目甲", is_pinned: false }] });
    const projectChat = { ...conversation("project-chat"), project_id: "p1" };
    const taskChat = { ...conversation("task-chat"), task_id: "task-1" };
    vi.mocked(client.listConversations).mockImplementation(async input => ({
      items: input.taskOnly ? [taskChat] : input.projectId ? [projectChat] : [],
      next_cursor: null,
    }));
    const view = await renderBootstrapped();
    await act(async () => { await session!.refreshTaskConversations(new AbortController().signal); });
    expect(session?.recents).toEqual([taskChat]);
    expect(session?.conversations).toEqual([projectChat]);
    expect(session?.conversationId).toBe("project-chat");
    view.unmount();
  });
});

describe("new conversation draft", () => {
  it("does nothing when the right pane is already a new conversation", async () => {
    const view = await renderBootstrapped();
    const epoch = session!.epoch;
    act(() => { session!.startNewConversation(); });
    expect(session?.conversationId).toBeNull();
    expect(session?.epoch).toBe(epoch);
    expect(client.createConversation).not.toHaveBeenCalled();
    view.unmount();
  });

  it("opens a blank recent chat without creating a sidebar row", async () => {
    localStorage.setItem(projectStorageKey("u1"), "p1");
    vi.mocked(client.listProjects).mockResolvedValue({ items: [{ id: "p1", name: "项目甲", is_pinned: false }] });
    vi.mocked(client.listConversations).mockImplementation(async (input) => ({
      items: input.projectId === "p1" ? [{ ...conversation("c1"), project_id: "p1" }] : [],
      next_cursor: null,
    }));
    const view = await renderBootstrapped();
    await waitFor(() => expect(session?.conversationId).toBe("c1"));
    act(() => { session!.startNewConversation(); });
    expect(session?.projectId).toBe("");
    expect(session?.conversationId).toBeNull();
    expect(session?.recents).toEqual([]);
    expect(client.createConversation).not.toHaveBeenCalled();
    const epoch = session!.epoch;
    act(() => { session!.startNewConversation(); });
    expect(session?.epoch).toBe(epoch);
    view.unmount();
  });

  it("keeps an existing recent row while opening a new blank recent chat", async () => {
    vi.mocked(client.listConversations).mockResolvedValue({ items: [conversation("c1")], next_cursor: null });
    const view = await renderBootstrapped();
    await waitFor(() => expect(session?.conversationId).toBe("c1"));
    act(() => { session!.startNewConversation(); });
    expect(session?.conversationId).toBeNull();
    expect(session?.recents.map((item) => item.id)).toEqual(["c1"]);
    expect(client.createConversation).not.toHaveBeenCalled();
    view.unmount();
  });

  it("opens a blank project chat without creating a sidebar row", async () => {
    vi.mocked(client.listProjects).mockResolvedValue({ items: [{ id: "p1", name: "项目甲", is_pinned: false }] });
    const view = await renderBootstrapped();
    act(() => { session!.startNewConversation("p1"); });
    expect(session?.projectId).toBe("p1");
    expect(session?.conversationId).toBeNull();
    expect(client.createConversation).not.toHaveBeenCalled();
    await waitFor(() => expect(session?.conversationsLoading).toBe(false));
    const epoch = session!.epoch;
    act(() => { session!.startNewConversation("p1"); });
    expect(session?.epoch).toBe(epoch);
    view.unmount();
  });

  it("keeps existing project rows while opening a new blank chat in that project", async () => {
    localStorage.setItem(projectStorageKey("u1"), "p1");
    vi.mocked(client.listProjects).mockResolvedValue({ items: [{ id: "p1", name: "项目甲", is_pinned: false }] });
    vi.mocked(client.listConversations).mockImplementation(async (input) => ({
      items: input.projectId === "p1" ? [{ ...conversation("c1"), project_id: "p1" }] : [],
      next_cursor: null,
    }));
    const view = await renderBootstrapped();
    await waitFor(() => expect(session?.conversationId).toBe("c1"));
    act(() => { session!.startNewConversation("p1"); });
    expect(session?.conversationId).toBeNull();
    expect(session?.conversations.map((item) => item.id)).toEqual(["c1"]);
    expect(client.createConversation).not.toHaveBeenCalled();
    view.unmount();
  });

  it("creates once on first use and adds a row when the first message starts", async () => {
    let started = false;
    vi.mocked(client.createConversation).mockResolvedValue(conversation("c1"));
    vi.mocked(client.listConversations).mockImplementation(async () => ({
      items: started ? [conversation("c1")] : [], next_cursor: null,
    }));
    const view = await renderBootstrapped();
    let created: ConversationSummary | null = null;
    await act(async () => { created = await session!.ensureConversation(); });
    expect(created?.id).toBe("c1");
    expect(session?.draftConversationId).toBe("c1");
    expect(session?.recents).toEqual([]);
    await act(async () => { await session!.ensureConversation(); });
    expect(client.createConversation).toHaveBeenCalledTimes(1);
    act(() => { session!.markConversationSubmitted("c1", "", "第一条消息"); });
    expect(session?.optimisticConversations[0]).toMatchObject({ id: "c1", title: "第一条消息", project_id: null });
    expect(session?.recents).toEqual([]);
    started = true;
    act(() => { session!.markConversationStarted("c1", ""); });
    await waitFor(() => expect(session?.recents.map((item) => item.id)).toEqual(["c1"]));
    expect(session?.optimisticConversations).toEqual([]);
    expect(session?.draftConversationId).toBeNull();
    view.unmount();
  });

  it("removes an unaccepted first message without discarding the draft conversation", async () => {
    vi.mocked(client.createConversation).mockResolvedValue(conversation("c1"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.ensureConversation(); });
    act(() => { session!.markConversationSubmitted("c1", "", "发送失败前的内容"); });
    expect(session?.optimisticConversations.map((item) => item.id)).toEqual(["c1"]);
    await act(async () => { await session!.refreshConversations(); });
    expect(session?.optimisticConversations.map((item) => item.id)).toEqual(["c1"]);
    act(() => { session!.cancelConversationSubmission("c1"); });
    expect(session?.optimisticConversations).toEqual([]);
    expect(session?.draftConversationId).toBe("c1");
    view.unmount();
  });

  it("replaces the immediate local row with the created conversation ID", async () => {
    const view = await renderBootstrapped();
    act(() => { session!.markConversationSubmitted("local:1", "", "第一条消息", true); });
    expect(session?.optimisticConversations[0]).toMatchObject({ id: "local:1", localOnly: true });
    act(() => { session!.identifyConversationSubmission("local:1", "c1"); });
    expect(session?.optimisticConversations[0]).toMatchObject({ id: "c1", localOnly: false });
    view.unmount();
  });

  it("opens another blank chat while the first conversation is still being created", async () => {
    const view = await renderBootstrapped();
    act(() => { session!.markConversationSubmitted("local:1", "", "第一条消息", true); });
    const epoch = session!.epoch;
    act(() => { session!.startNewConversation(); });
    expect(session?.conversationId).toBeNull();
    expect(session?.epoch).toBeGreaterThan(epoch);
    const nextEpoch = session!.epoch;
    act(() => { session!.startNewConversation(); });
    expect(session?.epoch).toBe(nextEpoch);
    view.unmount();
  });

  it("can open another blank chat as soon as the first message is submitted", async () => {
    vi.mocked(client.createConversation).mockResolvedValue(conversation("c1"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.ensureConversation(); });
    act(() => { session!.markConversationSubmitted("c1", "", "第一条消息"); });
    act(() => { session!.startNewConversation(); });
    expect(session?.conversationId).toBeNull();
    expect(session?.optimisticConversations.map((item) => item.id)).toEqual(["c1"]);
    view.unmount();
  });

  it("shares an in-flight first-use creation", async () => {
    const first = deferred<ConversationSummary>();
    vi.mocked(client.createConversation).mockReturnValue(first.promise);
    const view = await renderBootstrapped();
    let a: Promise<ConversationSummary | null>;
    let b: Promise<ConversationSummary | null>;
    act(() => {
      a = session!.ensureConversation();
      b = session!.ensureConversation();
    });
    expect(client.createConversation).toHaveBeenCalledTimes(1);
    await act(async () => { first.resolve(conversation("c1")); await Promise.all([a, b]); });
    expect(session?.conversationId).toBe("c1");
    view.unmount();
  });
});
