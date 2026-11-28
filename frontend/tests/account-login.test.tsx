import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { LoginPage } from "../src/components/LoginPage";
import { AuthGate } from "../src/components/AuthGate";
import { ApiError } from "../src/api/client";
import * as auth from "../src/api/auth";

vi.mock("../src/api/auth", () => ({
  getAuthConfig: vi.fn(), login: vi.fn(), passwordlessLogin: vi.fn(), getAuthSession: vi.fn(), setRequestUser: vi.fn(),
  loginWithSms: vi.fn(), registerAccount: vi.fn(), resetPasswordWithSms: vi.fn(), sendSmsChallenge: vi.fn(),
  AUTH_EVENT: "melonclaw-auth-change", AUTH_STORAGE: "melonclaw.auth-change",
}));
beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  vi.mocked(auth.getAuthConfig).mockResolvedValue({ passwordless: false, assistant_name: "Melon" });
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
});

it("shows a complaint entry on both login brands", async () => {
  const melon = render(<LoginPage />);
  expect(await screen.findByRole("button", { name: "意见反馈" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "意见反馈" }).className).toContain("login-report-entry");
  expect(screen.getByRole("link", { name: "隐私说明" }).getAttribute("href")).toBe("/privacy");
  melon.unmount();
  render(<LoginPage rms />);
  expect(screen.getByAltText("RMS")).toBeTruthy();
  expect(screen.getByRole("button", { name: "意见反馈" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "隐私说明" }).getAttribute("href")).toBe("/privacy");
  expect(screen.queryByText("开启智能协作")).toBeNull();
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
  vi.mocked(auth.getAuthConfig).mockResolvedValue({ passwordless: true, assistant_name: "Melon" });
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

it("hides sms and registration until the server enables them", async () => {
  render(<LoginPage />);
  await waitFor(() => expect(auth.getAuthConfig).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "短信验证码登录" })).toBeNull();
  expect(screen.queryByRole("button", { name: "注册账号" })).toBeNull();
  expect(screen.queryByRole("button", { name: "忘记密码" })).toBeNull();
  expect(screen.getByRole("link", { name: "隐私说明" }).getAttribute("href")).toBe("/privacy");
});

it("switches entries, counts down, and clears a code when the phone changes", async () => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({
    passwordless: false, assistant_name: "Melon", registration: true, sms_login: true,
  });
  vi.mocked(auth.sendSmsChallenge).mockResolvedValue({ challenge_id: "a".repeat(32), expires_in: 300, resend_after: 60 });
  render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "短信验证码登录" }));
  fireEvent.click(screen.getByRole("button", { name: "手机号用途说明" }));
  expect(await screen.findByText("手机号用于注册和登录，阿里云发码并核验。")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "手机号" }), { target: { value: "13800138000" } });
  fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
  expect(await screen.findByRole("button", { name: "60 秒后重发" })).toBeTruthy();
  expect(auth.sendSmsChallenge).toHaveBeenCalledWith("13800138000", "login");
  fireEvent.change(screen.getByLabelText("验证码"), { target: { value: "123456" } });
  fireEvent.change(screen.getByRole("textbox", { name: "手机号" }), { target: { value: "13900139000" } });
  expect((screen.getByLabelText("验证码") as HTMLInputElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "注册账号" }));
  fireEvent.click(screen.getByRole("button", { name: "手机号用途说明" }));
  expect(await screen.findByText("手机号用于注册和登录，阿里云发码并核验。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "账号密码登录" }));
  expect(screen.getByLabelText("用户 ID")).toBeTruthy();
  expect(screen.queryByLabelText("验证码")).toBeNull();
});

it("opens privacy without sending a code and restores the sms form", async () => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({
    passwordless: false, assistant_name: "Melon", registration: true, sms_login: true,
  });
  const view = render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "短信验证码登录" }));
  fireEvent.change(screen.getByRole("textbox", { name: "手机号" }), { target: { value: "13800138000" } });
  fireEvent.click(screen.getByRole("button", { name: "手机号用途说明" }));
  const link = (await screen.findAllByRole("link", { name: "隐私说明" })).at(-1)!;
  expect(link.getAttribute("href")).toBe("/privacy");
  fireEvent.click(link);
  expect(auth.sendSmsChallenge).not.toHaveBeenCalled();
  view.unmount();
  render(<LoginPage />);
  expect(await screen.findByDisplayValue("13800138000")).toBeTruthy();
  expect(auth.sendSmsChallenge).not.toHaveBeenCalled();
});

it("saves password form draft when opening the footer privacy link", async () => {
  const view = render(<LoginPage />);
  await waitFor(() => expect(auth.getAuthConfig).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("用户 ID"), { target: { value: "bob" } });
  fireEvent.click(screen.getByRole("link", { name: "隐私说明" }));
  view.unmount();
  render(<LoginPage />);
  expect(await screen.findByDisplayValue("bob")).toBeTruthy();
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

it("resets password via sms verification code and returns to password login", async () => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({
    passwordless: false, assistant_name: "Melon", registration: true, sms_login: true,
  });
  vi.mocked(auth.sendSmsChallenge).mockResolvedValue({ challenge_id: "b".repeat(32), expires_in: 300, resend_after: 60 });
  vi.mocked(auth.resetPasswordWithSms).mockResolvedValue({ ok: true, user_id: "alice" });
  render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "忘记密码" }));
  expect(screen.getByText("找回密码")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "手机号用途说明" }));
  expect(await screen.findByText("手机号用于找回密码验证，阿里云发码并核验。")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "手机号" }), { target: { value: "13800138000" } });
  fireEvent.click(screen.getByRole("button", { name: "获取验证码" }));
  expect(await screen.findByRole("button", { name: "60 秒后重发" })).toBeTruthy();
  expect(auth.sendSmsChallenge).toHaveBeenCalledWith("13800138000", "reset");
  fireEvent.change(screen.getByLabelText("验证码"), { target: { value: "654321" } });
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "new-password" } });
  fireEvent.change(screen.getByLabelText("确认新密码"), { target: { value: "new-password" } });
  fireEvent.click(screen.getByRole("button", { name: /重\s*置\s*密\s*码/ }));
  await screen.findByText("密码已重置，请使用新密码登录。");
  expect(auth.resetPasswordWithSms).toHaveBeenCalledWith({
    phone: "13800138000",
    challenge_id: "b".repeat(32),
    code: "654321",
    password: "new-password",
    confirm_password: "new-password",
  });
  expect((screen.getByLabelText("用户 ID") as HTMLInputElement).value).toBe("alice");
});

it("restores forgot password draft after opening privacy link", async () => {
  vi.mocked(auth.getAuthConfig).mockResolvedValue({
    passwordless: false, assistant_name: "Melon", registration: false, sms_login: true,
  });
  const view = render(<LoginPage />);
  fireEvent.click(await screen.findByRole("button", { name: "忘记密码" }));
  fireEvent.change(screen.getByRole("textbox", { name: "手机号" }), { target: { value: "13800138000" } });
  fireEvent.click(screen.getByRole("link", { name: "隐私说明" }));
  expect(auth.sendSmsChallenge).not.toHaveBeenCalled();
  view.unmount();
  render(<LoginPage />);
  expect(await screen.findByText("找回密码")).toBeTruthy();
  expect(await screen.findByDisplayValue("13800138000")).toBeTruthy();
});
