import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionProvider, useSession, type SessionContextValue } from "../src/state/session";
import * as client from "../src/api/client";
import type { ConversationHistory, ConversationSummary } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  createConversation: vi.fn(),
  createProject: vi.fn(),
  getConversationHistory: vi.fn(),
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
    <SessionProvider>
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
      is_default: true, tenant_ids: ["t1"], tenant_id: "t1", default_tenant_id: "t1",
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
  vi.mocked(client.getConversationHistory).mockReset();
  // 默认当前会话非空，避免复用分支干扰旧用例。
  vi.mocked(client.getConversationHistory).mockResolvedValue({
    items: [{ id: "m1" }],
  } as unknown as ConversationHistory);
});

describe("newConversation 连点", () => {
  it("慢速连点：每次都创建一个新会话", async () => {
    vi.mocked(client.createConversation)
      .mockResolvedValueOnce(conversation("c1"))
      .mockResolvedValueOnce(conversation("c2"));
    const view = await renderBootstrapped();

    let first: ConversationSummary | null = null;
    await act(async () => { first = await session!.newConversation(); });
    expect(first?.id).toBe("c1");
    expect(session?.conversationId).toBe("c1");

    let second: ConversationSummary | null = null;
    await act(async () => { second = await session!.newConversation(); });
    expect(second?.id).toBe("c2");
    expect(session?.conversationId).toBe("c2");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("在途连点：第二次不丢失，会排队补建", async () => {
    const firstCall = deferred<ConversationSummary>();
    const secondCall = deferred<ConversationSummary>();
    vi.mocked(client.createConversation)
      .mockReturnValueOnce(firstCall.promise)
      .mockReturnValueOnce(secondCall.promise);
    const view = await renderBootstrapped();

    // 第一次创建挂起时再点一次：旧行为直接吞掉，新行为排队。
    let firstPromise: Promise<ConversationSummary | null> | undefined;
    let queuedPromise: Promise<ConversationSummary | null> | undefined;
    await act(async () => {
      firstPromise = session!.newConversation();
      // conversationCreating 已置位，第二次调用走排队分支。
      queuedPromise = session!.newConversation();
      await queuedPromise;
    });
    await expect(queuedPromise).resolves.toBeNull();
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(1);

    await act(async () => { firstCall.resolve(conversation("c1")); });
    // 排队的那一次被补建：第二次 POST 发出，最终落在新会话上。
    await waitFor(() => expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(2));
    await act(async () => { secondCall.resolve(conversation("c2")); });
    await waitFor(() => expect(session?.conversationId).toBe("c2"));
    await expect(firstPromise).resolves.toMatchObject({ id: "c1" });
    view.unmount();
  });
});

describe("newConversation 复用白纸", () => {
  it("停在空普通会话上点新建：不建，直接复用", async () => {
    vi.mocked(client.createConversation).mockResolvedValueOnce(conversation("c1"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.newConversation(); });
    expect(session?.conversationId).toBe("c1");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(1);

    // c1 没有任何消息：再点新建不应有第二次 POST。
    vi.mocked(client.getConversationHistory).mockResolvedValue({
      items: [],
    } as unknown as ConversationHistory);
    let reused: ConversationSummary | null = null;
    await act(async () => { reused = await session!.newConversation(); });
    expect(reused).toMatchObject({ id: "c1", project_id: null });
    expect(session?.conversationId).toBe("c1");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(client.getConversationHistory)).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId: "c1", limit: 1 }),
    );
    view.unmount();
  });

  it("当前会话非空时点新建：正常创建", async () => {
    vi.mocked(client.createConversation)
      .mockResolvedValueOnce(conversation("c1"))
      .mockResolvedValueOnce(conversation("c2"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.newConversation(); });
    // 默认 mock 历史非空 → 第二次点新建必须建。
    let second: ConversationSummary | null = null;
    await act(async () => { second = await session!.newConversation(); });
    expect(second?.id).toBe("c2");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("空会话但目标是另一个项目：仍新建（不跨作用域复用）", async () => {
    vi.mocked(client.listProjects).mockResolvedValue({ items: [{ id: "p1", name: "P1" }] });
    vi.mocked(client.createConversation).mockResolvedValueOnce(conversation("c1"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.newConversation(); });
    vi.mocked(client.getConversationHistory).mockResolvedValue({
      items: [],
    } as unknown as ConversationHistory);
    vi.mocked(client.createConversation).mockResolvedValueOnce({ ...conversation("c2"), project_id: "p1" });
    let second: ConversationSummary | null = null;
    await act(async () => { second = await session!.newConversation("p1"); });
    expect(second?.id).toBe("c2");
    expect(session?.conversationId).toBe("c2");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("同项目空会话上点项目新建：不建，直接复用", async () => {
    vi.mocked(client.listProjects).mockResolvedValue({ items: [{ id: "p1", name: "P1" }] });
    vi.mocked(client.createConversation).mockResolvedValueOnce({ ...conversation("c1"), project_id: "p1" });
    const view = await renderBootstrapped();
    await act(async () => { await session!.newConversation("p1"); });
    expect(session?.conversationId).toBe("c1");
    vi.mocked(client.getConversationHistory).mockResolvedValue({
      items: [],
    } as unknown as ConversationHistory);
    let reused: ConversationSummary | null = null;
    await act(async () => { reused = await session!.newConversation("p1"); });
    expect(reused).toMatchObject({ id: "c1", project_id: "p1" });
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it("空会话但正在跑输出：仍新建（首条消息可能在途）", async () => {
    vi.mocked(client.createConversation)
      .mockResolvedValueOnce(conversation("c1"))
      .mockResolvedValueOnce(conversation("c2"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.newConversation(); });
    vi.mocked(client.getConversationHistory).mockResolvedValue({
      items: [],
    } as unknown as ConversationHistory);
    await act(async () => { session!.markConversationRunning("c1"); });
    let second: ConversationSummary | null = null;
    await act(async () => { second = await session!.newConversation(); });
    expect(second?.id).toBe("c2");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it("历史检查失败时不断新建（fail open）", async () => {    vi.mocked(client.createConversation)
      .mockResolvedValueOnce(conversation("c1"))
      .mockResolvedValueOnce(conversation("c2"));
    const view = await renderBootstrapped();
    await act(async () => { await session!.newConversation(); });
    vi.mocked(client.getConversationHistory).mockRejectedValueOnce(new Error("net"));
    let second: ConversationSummary | null = null;
    await act(async () => { second = await session!.newConversation(); });
    expect(second?.id).toBe("c2");
    expect(vi.mocked(client.createConversation)).toHaveBeenCalledTimes(2);
    view.unmount();
  });
});
