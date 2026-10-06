import { App } from "antd";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiRequest } from "../src/api/client";
import {
  AUTH_EVENT,
  AUTH_STORAGE,
  setRequestUser,
  type AuthUser,
} from "../src/api/auth";
import { AccountManagement } from "../src/components/AccountManagement";
import { AuthGate } from "../src/components/AuthGate";
import { ChangePasswordDialog } from "../src/components/ChangePasswordDialog";

const { refreshUsers } = vi.hoisted(() => ({ refreshUsers: vi.fn().mockResolvedValue(undefined) }));

vi.mock("../src/state/session", () => ({
  useSession: () => ({ userId: "admin", refreshUsers }),
}));

function response(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const admin: AuthUser = {
  user_id: "admin",
  user_name_zh: "管理员",
  tenant_id: "system",
  tenant_name_zh: "系统",
};
const alice: AuthUser = {
  user_id: "alice",
  user_name_zh: "Alice",
  tenant_id: "team",
  tenant_name_zh: "团队",
};
const bob: AuthUser = {
  user_id: "bob",
  user_name_zh: "Bob",
  tenant_id: "team",
  tenant_name_zh: "团队",
};

function PrivateChat({ user }: { user: AuthUser }) {
  const [draft, setDraft] = useState("");
  return <section>
    <div>当前身份：{user.user_id}</div>
    <label>聊天草稿<input value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
  </section>;
}

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "http://localhost").pathname;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  setRequestUser("");
  refreshUsers.mockClear();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches: false,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("drops the old chat subtree on identity refresh and ignores an older session response", async () => {
  const initial = deferred<Response>();
  const nextIdentity = deferred<Response>();
  let sessionCalls = 0;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    if (pathOf(input) !== "/api/auth/session") throw new Error(`Unexpected request: ${pathOf(input)}`);
    sessionCalls += 1;
    if (sessionCalls === 1) return initial.promise;
    if (sessionCalls === 2) return Promise.resolve(response(200, alice));
    if (sessionCalls === 3) return nextIdentity.promise;
    throw new Error(`Unexpected session request ${sessionCalls}`);
  }));

  render(<AuthGate>{(user) => <PrivateChat user={user} />}</AuthGate>);
  await waitFor(() => expect(sessionCalls).toBe(1));
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: AUTH_STORAGE })));
  expect(await screen.findByText("当前身份：alice")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("聊天草稿"), { target: { value: "Alice 的未发送内容" } });
  act(() => window.dispatchEvent(new Event(AUTH_EVENT)));
  await waitFor(() => expect(sessionCalls).toBe(3));
  expect(screen.queryByText("Alice 的未发送内容")).toBeNull();
  expect(screen.queryByLabelText("聊天草稿")).toBeNull();

  await act(async () => { nextIdentity.resolve(response(200, bob)); });
  expect(await screen.findByText("当前身份：bob")).toBeTruthy();
  expect((screen.getByLabelText("聊天草稿") as HTMLInputElement).value).toBe("");

  await act(async () => { initial.resolve(response(200, admin)); });
  await waitFor(() => expect(screen.getByText("当前身份：bob")).toBeTruthy());
  expect(screen.queryByText("当前身份：admin")).toBeNull();
});

it("turns an API 401 into a global auth-expired event and includes the session identity assertion", async () => {
  const fetchMock = vi.fn().mockResolvedValue(response(401, { detail: "请先登录" }));
  vi.stubGlobal("fetch", fetchMock);
  setRequestUser("alice");
  const expired = vi.fn();
  window.addEventListener("melonclaw-auth-expired", expired);
  try {
    await expect(apiRequest("/api/protected")).rejects.toMatchObject({ status: 401, message: "请先登录" });
    expect(expired).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith("/api/protected", expect.objectContaining({
      credentials: "include",
      cache: "no-store",
      headers: { "X-Melonclaw-User": "alice" },
    }));
  } finally {
    window.removeEventListener("melonclaw-auth-expired", expired);
  }
});

it("returns to login after a successful password change invalidates the cookie session", async () => {
  let authenticated = true;
  vi.stubGlobal("crypto", { randomUUID: () => "auth-change-test" });
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathOf(input);
    if (path === "/api/auth/session") {
      return Promise.resolve(authenticated ? response(200, alice) : response(401, { detail: "请先登录" }));
    }
    if (path === "/api/auth/config") return Promise.resolve(response(200, { passwordless: false }));
    if (path === "/api/auth/password" && init?.method === "POST") {
      authenticated = false;
      return Promise.resolve(response(200, { ok: true }));
    }
    throw new Error(`Unexpected request: ${path}`);
  }));

  const closed = vi.fn();
  render(<App>
    <AuthGate>{() => <section><div>受保护的聊天</div><ChangePasswordDialog onClose={closed} /></section>}</AuthGate>
  </App>);
  expect(await screen.findByText("受保护的聊天")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "new-password" } });
  fireEvent.change(screen.getByLabelText("再次输入密码"), { target: { value: "new-password" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

  expect(await screen.findByLabelText("用户 ID")).toBeTruthy();
  expect(screen.queryByText("受保护的聊天")).toBeNull();
  expect(closed).toHaveBeenCalledOnce();
});

it("blocks invalid account creation and submits the validated form fields", async () => {
  const created: unknown[] = [];
  const user = {
    user_id: "alice",
    user_name_zh: "Alice",
    tenant_id: "system",
    tenant_name_zh: "系统",
    tenant_enabled: true,
    created_at: "2026-10-06T00:00:00Z",
  };
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathOf(input);
    if (path === "/api/admin/users" && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return Promise.resolve(response(200, user));
    }
    if (path === "/api/admin/users") return Promise.resolve(response(200, { items: [] }));
    if (path === "/api/admin/tenants") return Promise.resolve(response(200, { items: [
      { tenant_id: "system", tenant_name_zh: "系统", enabled: true, created_at: "2026-10-06T00:00:00Z" },
    ] }));
    throw new Error(`Unexpected request: ${path}`);
  }));

  render(<App><AccountManagement /></App>);
  await screen.findByRole("button", { name: "创建用户" });
  fireEvent.click(screen.getByRole("button", { name: "创建用户" }));
  fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "alice!" } });
  fireEvent.change(screen.getByLabelText("用户名称"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("初始密码"), { target: { value: "password-one" } });
  fireEvent.change(screen.getByLabelText("再次输入密码"), { target: { value: "password-two" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await screen.findByText("以字母或数字开头，1～64 位字母、数字、下划线或连字符");
  expect(created).toEqual([]);

  fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "alice" } });
  fireEvent.change(screen.getByLabelText("再次输入密码"), { target: { value: "password-one" } });
  fireEvent.mouseDown(screen.getByLabelText("所属租户"));
  fireEvent.click(await screen.findByText("系统（system）"));
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

  await waitFor(() => expect(created).toEqual([{
    user_name_zh: "Alice",
    tenant_id: "system",
    user_id: "alice",
    password: "password-one",
    confirm_password: "password-one",
  }]));
  expect(refreshUsers).toHaveBeenCalledOnce();
});

it.each([[200, false], [500, false], [200, true], [500, true]] as const)("ignores stale account response (%s), older settles first: %s", async (oldStatus, olderFirst) => {
  const initialTenants = deferred<Response>();
  const newerTenants = deferred<Response>();
  let listRequests = 0;
  const tenants: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = pathOf(input);
    if (path === "/api/admin/users") return Promise.resolve(response(200, { items: [] }));
    if (path === "/api/admin/tenants" && init?.method === "POST") {
      tenants.push({ ...JSON.parse(String(init.body)), created_at: "2026-10-06T00:00:00Z" });
      return Promise.resolve(response(200, { ok: true }));
    }
    if (path === "/api/admin/tenants") {
      listRequests += 1;
      return listRequests === 1 ? initialTenants.promise : newerTenants.promise;
    }
    throw new Error(`Unexpected request: ${path}`);
  }));

  render(<App><AccountManagement /></App>);
  await waitFor(() => expect(listRequests).toBe(1));
  fireEvent.click(screen.getByRole("tab", { name: "租户管理" }));
  fireEvent.click(screen.getByRole("button", { name: "创建租户" }));
  fireEvent.change(screen.getByLabelText("租户 ID"), { target: { value: "new-team" } });
  fireEvent.change(screen.getByLabelText("租户名称"), { target: { value: "新团队" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(listRequests).toBe(2));

  const settleOld = () => initialTenants.resolve(response(oldStatus,
    oldStatus === 200 ? { items: [] } : { detail: "迟到的加载错误" }));
  if (olderFirst) {
    await act(async () => { settleOld(); });
    expect(screen.getByRole("button", { name: /刷\s*新/ }).classList.contains("ant-btn-loading")).toBe(true);
  }
  await act(async () => { newerTenants.resolve(response(200, { items: tenants })); });
  expect(await screen.findByText("新团队")).toBeTruthy();
  if (!olderFirst) await act(async () => { settleOld(); });
  expect(screen.getByText("新团队")).toBeTruthy();
  expect(screen.queryByText("迟到的加载错误")).toBeNull();
  expect(screen.getByRole("button", { name: /刷\s*新/ }).classList.contains("ant-btn-loading")).toBe(false);
});
