import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { McpToolDetails } from "../src/components/McpToolDetails";
import type { McpServer } from "../src/types/api";

const item = { id: "demo", display_name: "Demo", transport: "http", effective_enabled: false } as McpServer;
it("shows descriptions, missing permissions, and searches without executing tools", () => {
  const onDiscover = vi.fn();
  render(<McpToolDetails dialog={{ item, loading: false, result: {
    ok: true, tool_count: 2, enabled_tool_count: 1, error_code: null, message: "Connected",
    missing_allowed_tools: ["removed"], tools: [
      { name: "search", description: "Search documents", enabled: true },
      { name: "write", description: "Edit documents", enabled: false },
    ],
  } }} onClose={vi.fn()} onDiscover={onDiscover} />);
  expect(screen.getByText("当前未使用此服务，允许的工具在启用后才会提供给 Agent。")).toBeTruthy();
  expect(screen.getByText("部分白名单工具已不存在，其他允许工具仍可使用。")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("搜索 MCP 工具"), { target: { value: "Search documents" } });
  expect(screen.getByText("search")).toBeTruthy();
  expect(screen.queryByText("write")).toBeNull();
  expect(onDiscover).not.toHaveBeenCalled();
});

it("requires explicit discovery before starting a stdio program", () => {
  const onDiscover = vi.fn();
  render(<McpToolDetails dialog={{ item: { ...item, transport: "stdio" }, loading: false }} onClose={vi.fn()} onDiscover={onDiscover} />);
  expect(onDiscover).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("发现工具并启动程序"));
  expect(onDiscover).toHaveBeenCalledOnce();
});
