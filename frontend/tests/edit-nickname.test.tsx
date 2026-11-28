import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { EditNicknameDialog } from "../src/components/EditNicknameDialog";
import { GlobalNav } from "../src/components/GlobalNav";
import { UserPicker } from "../src/components/UserPicker";
import { getAuthConfig, getAuthSession, logout, updateProfile } from "../src/api/auth";

const { refreshUsers, mockSession } = vi.hoisted(() => ({
  refreshUsers: vi.fn(),
  mockSession: {
    userId: "alice",
    users: [
      {
        user_id: "alice",
        user_name_zh: "王小明",
        username: "王小明",
        display_name: "王小明-用户alice",
        is_default: false,
        tenant_id: "u1",
        tenant_role: "member",
        tenant_status: "active",
      },
    ],
    status: { status: "ready" as const },
    modelOptions: [],
    changeUser: vi.fn(),
    refreshUsers: vi.fn(),
  },
}));

vi.mock("../src/api/auth", () => ({
  getAuthConfig: vi.fn(),
  getAuthSession: vi.fn(),
  logout: vi.fn(),
  updateProfile: vi.fn(),
}));
vi.mock("../src/state/session", () => ({
  useSession: () => ({ ...mockSession, refreshUsers }),
}));

beforeEach(() => {
  vi.resetAllMocks();
  refreshUsers.mockResolvedValue(undefined);
  vi.mocked(getAuthConfig).mockResolvedValue({
    passwordless: false,
    assistant_name: "Melon",
    registration: false,
    sms_login: false,
  });
  vi.mocked(getAuthSession).mockResolvedValue({
    user_id: "alice",
    user_name_zh: "王小明",
    tenant_id: "u1",
    tenant_name_zh: "用户alice",
  });
  vi.mocked(logout).mockResolvedValue(undefined);
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

it("validates blank input and trims the nickname before submitting", async () => {
  const close = vi.fn();
  const saved = vi.fn().mockResolvedValue(undefined);
  vi.mocked(updateProfile).mockResolvedValue({ ok: true, user_name_zh: "新昵称" });

  render(
    <App>
      <EditNicknameDialog open currentName="小明" onClose={close} onSaved={saved} />
    </App>,
  );

  const input = screen.getByLabelText("用户昵称") as HTMLInputElement;
  expect(input.value).toBe("小明");

  fireEvent.change(input, { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await screen.findByText("请输入用户昵称");
  expect(updateProfile).not.toHaveBeenCalled();

  fireEvent.change(input, { target: { value: "  新昵称  " } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

  await waitFor(() => expect(updateProfile).toHaveBeenCalledWith({ user_name_zh: "新昵称" }));
  expect(saved).toHaveBeenCalledWith("新昵称");
  expect(close).toHaveBeenCalledOnce();
});

it("shows nickname in the avatar menu and opens the edit dialog to refresh users", async () => {
  vi.mocked(updateProfile).mockResolvedValue({ ok: true, user_name_zh: "李大明" });

  render(
    <App>
      <GlobalNav
        view="chat"
        onHome={vi.fn()}
        onResources={vi.fn()}
        onAmp={vi.fn()}
        onMindera={vi.fn()}
      />
    </App>,
  );

  const avatarButton = await screen.findByRole("button", { name: "当前用户：王小明" });
  expect(screen.queryByRole("button", { name: "当前用户：王小明-用户alice" })).toBeNull();

  fireEvent.click(avatarButton);
  expect(await screen.findByText("王小明")).toBeTruthy();
  expect(screen.queryByText("王小明-用户alice")).toBeNull();

  fireEvent.click(await screen.findByText("修改昵称"));
  const input = await screen.findByLabelText("用户昵称");
  expect((input as HTMLInputElement).value).toBe("王小明");

  fireEvent.change(input, { target: { value: "李大明" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

  await waitFor(() => expect(updateProfile).toHaveBeenCalledWith({ user_name_zh: "李大明" }));
  await waitFor(() => expect(refreshUsers).toHaveBeenCalledOnce());
});

it("shows only user name without tenant suffix in UserPicker", async () => {
  vi.mocked(getAuthConfig).mockResolvedValue({
    passwordless: true,
    assistant_name: "Melon",
    registration: false,
    sms_login: false,
  });

  render(
    <App>
      <UserPicker />
    </App>,
  );

  const trigger = await screen.findByRole("button", { name: "更换用户" });
  expect(trigger.textContent).toContain("王小明");
  expect(trigger.textContent).not.toContain("王小明-用户alice");
});
