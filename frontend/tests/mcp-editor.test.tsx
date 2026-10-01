import { StrictMode } from "react";
import { App } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { McpEditor } from "../src/components/McpEditor";
import { saveMcp, testMcp } from "../src/api/mcp";
import type { McpServer } from "../src/types/api";

vi.mock("../src/api/mcp", () => ({ saveMcp: vi.fn(), testMcp: vi.fn() }));
afterEach(cleanup);
beforeEach(() => { vi.clearAllMocks(); vi.mocked(saveMcp).mockResolvedValue({}); vi.mocked(testMcp).mockResolvedValue({ ok: true, tool_count: 2, tool_names: ["tool_one", "tool_two"], message: "连接成功，发现 2 个工具。", error_code: null, tool_details: [] }); });

it("imports once, tests without saving and saves only form fields with no scope", async () => {
  render(<StrictMode><App><McpEditor userId="alice" isAdmin={false} onClose={vi.fn()} onSaved={vi.fn()} /></App></StrictMode>);
  fireEvent.click(screen.getByText("从 JSON 导入"));
  fireEvent.change(screen.getByLabelText("MCP JSON"), { target: { value: '{"mcpServers":{"demo":{"type":"http","url":"https://example.com/mcp"}}}' } });
  fireEvent.click(screen.getByText("解析并填充"));
  await waitFor(() => expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe("demo"));
  fireEvent.change(screen.getByLabelText("服务地址"), { target: { value: "https://changed.test/mcp" } });
  fireEvent.click(screen.getByText("测试连接"));
  await screen.findByText("工具数量：2");
  expect(saveMcp).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(saveMcp).toHaveBeenCalledOnce());
  const [user, config] = vi.mocked(saveMcp).mock.calls[0];
  expect(user).toBe("alice");
  expect(config.url).toBe("https://changed.test/mcp");
  expect(config).not.toHaveProperty("scope");
  expect(config).not.toHaveProperty("json");
});

it("edits saved credentials without returning their values or inventing JSON", async () => {
  const existing = { id: "id", slug: "demo", display_name: "Demo", description: "", transport: "http", url: "https://example.com/mcp", version: 2,
    headers_keys: ["Authorization"], env_keys: [], args: [], tool_allowlist: null } as McpServer;
  render(<App><McpEditor userId="alice" isAdmin={false} existing={existing} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  expect((screen.getByLabelText("请求头值 1") as HTMLInputElement).value).toBe("");
  expect(screen.getByPlaceholderText("已配置；留空保留，输入替换")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(saveMcp).toHaveBeenCalledOnce());
  expect(vi.mocked(saveMcp).mock.calls[0][1].headers).toEqual({ set: {}, remove: [], clear: false });
});

function existingServer(overrides: Partial<McpServer> = {}): McpServer {
  return { id: "id", slug: "demo", display_name: "Demo", description: "", scope: "user", transport: "http",
    url: "https://example.com/mcp", version: 2, enabled: true, personally_enabled: true, effective_enabled: true,
    shadowed: false, shadows_global: false, unavailable_reason: null, can_edit: true, can_delete: true, can_test: true,
    headers_keys: [], env_keys: [], args: [], tool_allowlist: null, ...overrides };
}

it("replaces a saved Authorization header using the API Key shortcut", async () => {
  render(<App><McpEditor userId="alice" isAdmin={false} existing={existingServer({ headers_keys: ["authorization"] })} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  fireEvent.change(screen.getByPlaceholderText("已配置；留空保留，输入新 Key 直接替换"), { target: { value: "replacement-key" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(saveMcp).toHaveBeenCalledOnce());
  expect(vi.mocked(saveMcp).mock.calls[0][1].headers).toEqual({ set: { authorization: "Bearer replacement-key" }, remove: [], clear: false });
});

it("shows saved choices before discovery and keeps them across metadata edits", async () => {
  render(<App><McpEditor userId="alice" isAdmin={false} existing={existingServer({ tool_allowlist: ["tool_one", "removed"] })} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  fireEvent.click(screen.getByText("高级配置"));
  expect((screen.getByRole("checkbox", { name: "removed" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByText("测试连接"));
  await screen.findByText("工具数量：2");
  expect(screen.getByRole("checkbox", { name: "removed（已不存在）" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Renamed" } });
  fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "New description" } });
  expect(screen.getByText("工具数量：2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(saveMcp).toHaveBeenCalledOnce());
  expect(vi.mocked(saveMcp).mock.calls[0][1].tool_allowlist).toEqual(["tool_one", "removed"]);
});

it("requires confirmation before saving an empty allowlist", async () => {
  render(<App><McpEditor userId="alice" isAdmin={false} existing={existingServer({ tool_allowlist: [] })} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await screen.findAllByText("禁止此服务的全部工具？");
  expect(saveMcp).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
  await waitFor(() => expect(saveMcp).toHaveBeenCalledOnce());
  expect(vi.mocked(saveMcp).mock.calls[0][1].tool_allowlist).toEqual([]);
});

it("discards stale test responses after the connection changes", async () => {
  let resolveTest!: (value: Awaited<ReturnType<typeof testMcp>>) => void;
  vi.mocked(testMcp).mockImplementationOnce(() => new Promise(resolve => { resolveTest = resolve; }));
  render(<App><McpEditor userId="alice" isAdmin={false} existing={existingServer()} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  fireEvent.click(screen.getByText("测试连接"));
  await waitFor(() => expect(testMcp).toHaveBeenCalledOnce());
  const signal = vi.mocked(testMcp).mock.calls[0][3];
  fireEvent.change(screen.getByLabelText("服务地址"), { target: { value: "https://changed.test/mcp" } });
  expect(signal.aborted).toBe(true);
  resolveTest({ ok: true, tool_count: 2, tool_names: ["tool_one"], tool_details: [], error_code: null, message: "Connected" });
  await waitFor(() => expect(screen.queryByText("工具数量：2")).toBeNull());
});

it("tells users a new configuration is saved but not yet in use", async () => {
  render(<App><McpEditor userId="alice" isAdmin={false} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "Demo" } });
  fireEvent.change(screen.getByLabelText("服务标识"), { target: { value: "demo" } });
  fireEvent.change(screen.getByLabelText("服务地址"), { target: { value: "https://example.com/mcp" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await screen.findByText("已保存，尚未使用；请在卡片上添加到我的服务。");
});

it("defaults a new explicit allowlist to all discovered tools and preserves hidden choices", async () => {
  render(<App><McpEditor userId="alice" isAdmin={false} existing={existingServer()} onClose={vi.fn()} onSaved={vi.fn()} /></App>);
  fireEvent.click(screen.getByText("测试连接"));
  await screen.findByText("工具数量：2");
  fireEvent.click(screen.getByText("高级配置"));
  fireEvent.mouseDown(screen.getByLabelText("工具范围"));
  fireEvent.click(await screen.findByText("仅允许指定工具"));
  expect((screen.getByRole("checkbox", { name: "tool_one" }) as HTMLInputElement).checked).toBe(true);
  expect((screen.getByRole("checkbox", { name: "tool_two" }) as HTMLInputElement).checked).toBe(true);
  fireEvent.change(screen.getByLabelText("搜索白名单工具"), { target: { value: "one" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "tool_one" }));
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(saveMcp).toHaveBeenCalledOnce());
  expect(vi.mocked(saveMcp).mock.calls[0][1].tool_allowlist).toEqual(["tool_two"]);
});
