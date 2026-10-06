import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { LoginPage } from "../src/components/LoginPage";
import { AuthGate } from "../src/components/AuthGate";
import { ApiError } from "../src/api/client";
import * as auth from "../src/api/auth";

vi.mock("../src/api/auth", () => ({
  getAuthConfig: vi.fn(), login: vi.fn(), passwordlessLogin: vi.fn(), getAuthSession: vi.fn(), setRequestUser: vi.fn(),
  AUTH_EVENT: "melonclaw-auth-change", AUTH_STORAGE: "melonclaw.auth-change",
}));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(auth.getAuthConfig).mockResolvedValue({ passwordless: false });
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
});

it("shows logo and password form without development text or passwordless by default", async () => {
  render(<LoginPage />);
  await waitFor(() => expect(auth.getAuthConfig).toHaveBeenCalled());
  expect(screen.getByAltText("MelonClaw")).toBeTruthy();
  expect(screen.getByLabelText("用户 ID")).toBeTruthy();
  expect(screen.queryByText("免密登录")).toBeNull();
  expect(screen.queryByText(/开发模式/)).toBeNull();
});

it("allows passwordless without requiring form values only when backend enables it", async () => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({ passwordless: true });
  vi.mocked(auth.passwordlessLogin).mockResolvedValue(undefined);
  render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "免密登录" }));
  await waitFor(() => expect(auth.passwordlessLogin).toHaveBeenCalledOnce());
  expect(auth.login).not.toHaveBeenCalled();
});

it("keeps values on password failure and never trims password", async () => {
  vi.mocked(auth.login).mockRejectedValue(new Error("用户 ID 或密码错误。"));
  render(<LoginPage />);
  fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: " alice " } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: " password-123 " } });
  fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
  await screen.findByText("用户 ID 或密码错误。");
  expect(auth.login).toHaveBeenCalledWith("alice", " password-123 ");
  expect((screen.getByLabelText("用户 ID") as HTMLInputElement).value).toBe(" alice ");
});

it("clears protected content on expiry and uses the server identity after a tab switch", async () => {
  vi.mocked(auth.getAuthSession).mockResolvedValue({ user_id: "admin", user_name_zh: "管理员", tenant_id: "system", tenant_name_zh: "系统" });
  render(<AuthGate>{(user) => <div>当前:{user.user_id}</div>}</AuthGate>);
  await screen.findByText("当前:admin");
  vi.mocked(auth.getAuthSession).mockResolvedValue({ user_id: "alice", user_name_zh: "Alice", tenant_id: "team", tenant_name_zh: "团队" });
  window.dispatchEvent(new StorageEvent("storage", { key: auth.AUTH_STORAGE }));
  await screen.findByText("当前:alice");
  expect(screen.queryByText("当前:admin")).toBeNull();
  vi.mocked(auth.getAuthSession).mockRejectedValue(new ApiError(401, "请先登录"));
  window.dispatchEvent(new Event("melonclaw-auth-expired"));
  await screen.findByLabelText("用户 ID");
  expect(screen.queryByText("当前:alice")).toBeNull();
});
