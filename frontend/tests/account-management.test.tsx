import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { AccountManagement } from "../src/components/AccountManagement";
import { apiRequest } from "../src/api/client";

vi.mock("../src/api/client", () => ({ apiRequest: vi.fn() }));
vi.mock("../src/state/session", () => ({ useSession: () => ({ userId: "admin", refreshUsers: vi.fn().mockResolvedValue(undefined) }) }));
const user = { user_id: "alice", user_name_zh: "测试用户", tenant_id: "system", tenant_name_zh: "系统", tenant_enabled: true, created_at: "2026-10-06T00:00:00Z" };
beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
  vi.mocked(apiRequest).mockImplementation(async (path) => path === "/api/admin/users" ? { items: [user] } : { items: [{ tenant_id: "system", tenant_name_zh: "系统", enabled: true }] });
});

it("edits a name without sending an immutable ID or password, then unmounts the form", async () => {
  render(<App><AccountManagement /></App>);
  await screen.findByText("测试用户");
  fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
  expect((screen.getByLabelText("用户 ID") as HTMLInputElement).disabled).toBe(true);
  expect(screen.queryByLabelText("初始密码")).toBeNull();
  fireEvent.change(screen.getByLabelText("用户名称"), { target: { value: "新名称" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/admin/users/alice", { method: "PATCH", body: { user_name_zh: "新名称", tenant_id: "system" } }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("uses a separate password form and keeps errors reviewable", async () => {
  render(<App><AccountManagement /></App>);
  await screen.findByText("测试用户");
  fireEvent.click(screen.getByRole("button", { name: "修改密码" }));
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "another-password" } });
  fireEvent.change(screen.getByLabelText("再次输入密码"), { target: { value: "another-password" } });
  vi.mocked(apiRequest).mockRejectedValueOnce(new Error("当前有请求正在执行，请稍后重试。"));
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await screen.findByText("当前有请求正在执行，请稍后重试。");
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(apiRequest).toHaveBeenCalledWith("/api/admin/users/alice/password", { method: "POST", body: { password: "another-password", confirm_password: "another-password" } });
});
