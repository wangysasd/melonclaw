import { useEffect, useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import App from "../src/App";

const disposed = vi.fn();
const session = { userId: "u1", users: [], status: { status: "ready" }, modelOptions: [], startNewConversation: vi.fn() };
vi.mock("../src/components/AuthGate", () => ({ AuthGate: ({ children }: { children: (user: { user_id: string }) => React.ReactNode }) => children({ user_id: "u1" }) }));

vi.mock("../src/state/session", () => ({ SessionProvider: ({ children }: { children: React.ReactNode }) => children, useSession: () => session }));
vi.mock("../src/components/ChatView", () => ({ ChatView: () => {
  const [draft, setDraft] = useState("");
  useEffect(() => () => disposed(), []);
  return <input aria-label="草稿" value={draft} onChange={(e) => setDraft(e.target.value)} />;
} }));
vi.mock("../src/components/Sidebar", () => ({ Sidebar: () => {
  const [collapsed, setCollapsed] = useState(false);
  return <button onClick={() => setCollapsed(!collapsed)}>{collapsed ? "展开" : "收起"}</button>;
} }));
vi.mock("../src/components/ProjectDialog", () => ({ ProjectDialog: () => null }));
vi.mock("../src/components/ToolCatalogDialog", () => ({ ToolCatalogDialog: () => null }));
vi.mock("../src/components/ResourceView", () => ({ ResourceView: ({ initialTab, onTabChange }: { initialTab: string; onTabChange: (tab: string) => void }) => <button onClick={() => onTabChange("models")}>TAB:{initialTab}</button> }));
vi.mock("../src/components/tasks/TaskCenter", () => ({ default: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>返回聊天</button> }));

beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); session.userId = "u1"; });
it("preserves mounted chat and collapse state across navigation and remembers the tab after reload", () => {
  const view = render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "我已确认并进入" }));
  expect(screen.queryByRole("button", { name: /^任务通知/ })).toBeNull();
  expect(screen.getByRole("button", { name: "定时任务" })).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("button", { name: "收起" }));
  fireEvent.click(screen.getByRole("button", { name: "拓展" }));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "展开" })).toBeNull();
  expect(disposed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("TAB:skills"));
  fireEvent.click(screen.getByRole("button", { name: "Home" }));
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("draft");
  expect(screen.getByRole("button", { name: "展开" })).toBeTruthy();
  expect(session.startNewConversation).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "拓展" }));
  expect(screen.getByText("TAB:models")).toBeTruthy();
  view.unmount();
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "拓展" }));
  expect(screen.getByText("TAB:models")).toBeTruthy();
});
it("returns from tasks to the existing chat without clearing its draft or starting a conversation", async () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "我已确认并进入" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "unsent draft" } });
  fireEvent.click(screen.getByRole("button", { name: "定时任务" }));
  fireEvent.click(await screen.findByRole("button", { name: "返回聊天" }));
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("unsent draft");
  expect(disposed).not.toHaveBeenCalled();
  expect(session.startNewConversation).not.toHaveBeenCalled();
});
it("clears the mounted draft on user change", () => {
  const view = render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "我已确认并进入" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "private" } });
  session.userId = "u2";
  view.rerender(<App />);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("");
  expect(disposed).toHaveBeenCalledOnce();
});
