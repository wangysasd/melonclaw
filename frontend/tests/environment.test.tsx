import { App } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { environmentChatDisplay } from "../src/lib/environmentChat";
import { EnvironmentApproval } from "../src/components/EnvironmentApproval";
import { EnvironmentManager } from "../src/components/EnvironmentManager";
import { apiRequest } from "../src/api/client";
import { deleteEnvironment } from "../src/api/environment";

vi.mock("../src/api/client", () => ({ apiRequest: vi.fn() }));

afterEach(cleanup);

const existing = { id: "env-1", name: "SERVICE_TOKEN", description: "fixture variable", scope: "user" as const,
  enabled: true, version: 7, masked_value: "abcd***wxyz", updated_at: "2026-10-18T00:00:00Z", effective_source: "personal" as const };

beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(window, "matchMedia", { writable: true, value: vi.fn().mockImplementation(() => ({ matches: false,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() })) });
  vi.mocked(apiRequest).mockResolvedValue({ items: [existing], scope: "user" });
});

it("removes complete and malformed environment instructions from optimistic text", () => {
  for (const text of ["设置环境变量 TOKEN=fixture-secret", "请帮我设置环境变量\nTOKEN=fixture-secret", "新增环境变量 TOKEN=fixture-secret\nSECOND=other"]) {
    expect(environmentChatDisplay(text)).not.toContain("fixture-secret");
  }
  const example = "解释以下示例\n设置环境变量 TOKEN=example";
  expect(environmentChatDisplay(example)).toBe(example);
});

describe("approval scope", () => {
  it.each(["global", "user"])("renders %s impact without a value input", scope => {
    const { container } = render(<EnvironmentApproval action={{ name: "confirm_environment_change", description: "",
      args: JSON.stringify({ change: { name: "TOKEN", scope, action: "update", replaces_value: true,
        enabled: false, impact: "测试影响" } }), allowed_decisions: ["approve", "reject"] }} />);
    expect(screen.getByText(`范围：${scope === "global" ? "系统共享（所有用户）" : "仅自己"}`)).not.toBeNull();
    expect(container.querySelector("input")).toBeNull();
  });

  it("never renders a value even if the approval payload accidentally contains one", () => {
    const secret = "synthetic-only-secret";
    const { container } = render(<EnvironmentApproval action={{ name: "confirm_environment_change", description: "",
      args: JSON.stringify({ change: { name: "SERVICE_TOKEN", scope: "global", action: "update", value: secret,
        replaces_value: true, enabled: true, impact: "系统共享项将被更新" } }), allowed_decisions: ["approve", "reject"] }} />);
    expect(container.textContent).not.toContain(secret);
  });
});


it("keeps stored draft references out of visible and copied user messages", () => {
  const result = environmentChatDisplay("用户请求维护环境变量 TOKEN；值已安全提取。请调用 prepare_environment_change，draft_id=opaque-reference");
  expect(result).toContain("TOKEN");
  expect(result).not.toContain("opaque-reference");
  expect(result).not.toContain("prepare_environment_change");
});

describe("environment manager", () => {
  it("lists masked previews and places description before actions", async () => {
    render(<App><EnvironmentManager userId="alice" isAdmin={false} /></App>);
    await screen.findByText("SERVICE_TOKEN");
    expect(screen.queryByText("synthetic-only-secret")).toBeNull();
    expect(screen.getByText("abcd***wxyz")).toBeTruthy();
    const headers = screen.getAllByRole("columnheader").map(header => header.textContent);
    expect(headers[0]).toBe("启用");
    expect(headers.slice(-2)).toEqual(["说明", "操作"]);
    expect(apiRequest).toHaveBeenCalledWith("/api/environment", { query: { user_id: "alice" }, signal: expect.any(AbortSignal) });
  });

  it("shows the value input immediately and saves a replacement with the row version", async () => {
    render(<App><EnvironmentManager userId="alice" isAdmin={false} /></App>);
    await screen.findByText("SERVICE_TOKEN");
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    expect(screen.queryByRole("switch", { name: "替换值" })).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("已配置（不回显）；留空保留，输入新值并保存即可更新"), { target: { value: "new-fixture-value" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/environment/env-1", {
      method: "PATCH", signal: expect.any(AbortSignal), body: { user_id: "alice", description: "fixture variable",
        enabled: true, value: "new-fixture-value", expected_version: 7 },
    }));
  });

  it("updates metadata without sending the hidden value when the value input stays empty", async () => {
    render(<App><EnvironmentManager userId="alice" isAdmin={false} /></App>);
    await screen.findByText("SERVICE_TOKEN");
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("说明"), { target: { value: "updated metadata" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/environment/env-1", {
      method: "PATCH", signal: expect.any(AbortSignal), body: { user_id: "alice", description: "updated metadata",
        enabled: true, expected_version: 7 },
    }));
    const requestBody = vi.mocked(apiRequest).mock.calls.find(([path]) => path === "/api/environment/env-1")?.[1]?.body as Record<string, unknown>;
    expect(requestBody).not.toHaveProperty("value");
  });

  it("creates an enabled variable with an explicit empty value", async () => {
    render(<App><EnvironmentManager userId="alice" isAdmin={false} /></App>);
    await screen.findByText("SERVICE_TOKEN");
    fireEvent.click(screen.getByRole("button", { name: "新增环境变量" }));
    fireEvent.change(screen.getByLabelText("变量名"), { target: { value: "EMPTY_FLAG" } });
    fireEvent.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/environment", {
      method: "POST", signal: expect.any(AbortSignal), body: { user_id: "alice", name: "EMPTY_FLAG",
        description: "", enabled: true, value: "" },
    }));
  });

  it("keeps a failed save visible and does not report success", async () => {
    render(<App><EnvironmentManager userId="alice" isAdmin={false} /></App>);
    await screen.findByText("SERVICE_TOKEN");
    vi.mocked(apiRequest).mockRejectedValueOnce(new Error("版本已变化，请刷新后重试。"));
    fireEvent.click(screen.getByRole("switch", { name: "SERVICE_TOKEN 启用" }));
    await screen.findByText("版本已变化，请刷新后重试。");
    expect(screen.getByText("SERVICE_TOKEN")).toBeTruthy();
    expect(apiRequest).toHaveBeenLastCalledWith("/api/environment/env-1", {
      method: "PATCH", signal: expect.any(AbortSignal), body: { user_id: "alice", enabled: false, expected_version: 7 },
    });
  });

  it("shows the administrator shared scope and uses CAS for deletion", async () => {
    vi.mocked(apiRequest).mockResolvedValue({ items: [{ ...existing, scope: "global", effective_source: "shared" }], scope: "global" });
    render(<App><EnvironmentManager userId="admin" isAdmin /></App>);
    await screen.findByText("SERVICE_TOKEN");
    await deleteEnvironment("admin", { ...existing, scope: "global" });
    expect(apiRequest).toHaveBeenLastCalledWith("/api/environment/env-1", {
      method: "DELETE", query: { user_id: "admin", expected_version: 7 }, signal: undefined,
    });
  });
});
