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

beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); session.userId = "u1"; });
it("preserves mounted chat and collapse state across navigation and remembers the tab after reload", () => {
  const view = render(<App />);
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
it("clears the mounted draft on user change", () => {
  const view = render(<App />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "private" } });
  session.userId = "u2";
  view.rerender(<App />);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("");
  expect(disposed).toHaveBeenCalledOnce();
});
