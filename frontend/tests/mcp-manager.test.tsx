import { App } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { listMcp } from "../src/api/client";
import { addMcp, discoverMcpTools, globalMcpState } from "../src/api/mcp";
import { McpManager } from "../src/components/McpManager";
import type { McpServer, McpToolDiscoveryResult } from "../src/types/api";

vi.mock("../src/api/client", () => ({ listMcp: vi.fn() }));
vi.mock("../src/api/mcp", () => ({ addMcp: vi.fn(), discoverMcpTools: vi.fn(), globalMcpState: vi.fn(), deleteMcp: vi.fn(), mcpDetail: vi.fn(), stopMcp: vi.fn() }));
const notify = { success: vi.fn(), error: vi.fn() };
const success: McpToolDiscoveryResult = { ok: true, tool_count: 2, enabled_tool_count: 1, tools: [], missing_allowed_tools: [], error_code: null, message: "Connected" };
function server(id: string, overrides: Partial<McpServer> = {}): McpServer {
  return { id, slug: id.toLowerCase(), display_name: id, description: "", scope: "global", transport: "http", version: 1,
    enabled: true, personally_enabled: true, effective_enabled: true, shadowed: false, shadows_global: false,
    unavailable_reason: null, can_edit: true, can_delete: true, can_test: true, ...overrides };
}
beforeEach(() => { vi.clearAllMocks(); vi.mocked(globalMcpState).mockResolvedValue({}); });

it("loads and refreshes cards without connection probes, discovering only on opening details", async () => {
  vi.mocked(listMcp).mockResolvedValue({ items: [server("Beta"), server("Alpha")] });
  vi.mocked(discoverMcpTools).mockResolvedValue(success);
  render(<App><McpManager userId="admin" isAdmin notify={notify} /></App>);
  await screen.findByText("Alpha");
  expect(Array.from(document.querySelectorAll(".skill-card-name")).map(element => element.textContent)).toEqual(["Alpha", "Beta"]);
  expect(Array.from(document.querySelectorAll(".mcp-card .app-logo")).map(element => element.textContent)).toEqual(["A", "B"]);
  expect(document.querySelector(".mcp-health-pill")).toBeNull();
  expect(discoverMcpTools).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /刷\s*新/ }));
  await waitFor(() => expect(listMcp).toHaveBeenCalledTimes(2));
  expect(discoverMcpTools).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看 MCP 工具：Alpha" }));
  await waitFor(() => expect(discoverMcpTools).toHaveBeenCalledOnce());
});

it("offers global enable directly for a newly saved administrator configuration", async () => {
  const item = server("Demo", { enabled: false, effective_enabled: false, unavailable_reason: "全员已停用" });
  vi.mocked(listMcp).mockResolvedValue({ items: [item] });
  render(<App><McpManager userId="admin" isAdmin notify={notify} /></App>);
  fireEvent.click(await screen.findByRole("button", { name: /全员启用/ }));
  await waitFor(() => expect(globalMcpState).toHaveBeenCalledWith(item, "admin"));
  expect(addMcp).not.toHaveBeenCalled();
  expect(discoverMcpTools).not.toHaveBeenCalled();
});

it("lets a user restore personal use from the menu after choosing not to use a shared MCP", async () => {
  const item = server("Research", {
    can_edit: false,
    can_delete: false,
    personally_enabled: false,
    effective_enabled: false,
    unavailable_reason: "我不使用",
  });
  vi.mocked(listMcp).mockResolvedValue({ items: [item] });
  vi.mocked(addMcp).mockResolvedValue({});
  render(<App><McpManager userId="member" isAdmin={false} notify={notify} /></App>);

  fireEvent.click(await screen.findByRole("button", { name: "Research 菜单" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "添加到我的服务" }));

  await waitFor(() => expect(addMcp).toHaveBeenCalledWith(item, "member"));
});
