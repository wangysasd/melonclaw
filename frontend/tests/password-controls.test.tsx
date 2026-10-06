import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ChangePasswordDialog } from "../src/components/ChangePasswordDialog";
import { UserPicker } from "../src/components/UserPicker";
import { apiRequest } from "../src/api/client";
import { getAuthConfig, notifyAuthChange } from "../src/api/auth";

vi.mock("../src/api/client", () => ({ apiRequest: vi.fn() }));
vi.mock("../src/api/auth", () => ({ getAuthConfig: vi.fn(), notifyAuthChange: vi.fn() }));
vi.mock("../src/state/session", () => ({ useSession: () => ({ userId: "alice", users: [{ user_id: "alice", username: "Alice" }], changeUser: vi.fn() }) }));
beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
});
it.each([false, true])("shows switching only when the backend dev capability is %s", async (enabled) => {
  vi.mocked(getAuthConfig).mockResolvedValue({ passwordless: enabled });
  render(<App><UserPicker compact /></App>);
  await waitFor(() => expect(getAuthConfig).toHaveBeenCalled());
  if (enabled) expect(await screen.findByRole("button", { name: "更换用户" })).toBeTruthy();
  else expect(screen.queryByRole("button", { name: "更换用户" })).toBeNull();
});
it("rejects mismatched passwords before submitting and refreshes authentication after saving", async () => {
  const close = vi.fn();
  vi.mocked(apiRequest).mockResolvedValue({ ok: true });
  render(<App><ChangePasswordDialog onClose={close} /></App>);
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value: "new-password" } });
  fireEvent.change(screen.getByLabelText("再次输入密码"), { target: { value: "different" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await screen.findByText("两次输入的密码不一致");
  expect(apiRequest).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("再次输入密码"), { target: { value: "new-password" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/auth/password", { method: "POST", body: { password: "new-password", confirm_password: "new-password" } }));
  expect(close).toHaveBeenCalled();
  expect(notifyAuthChange).toHaveBeenCalled();
});
