import { App as AntdApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ModelSection } from "../src/components/ModelProviders";
import { listManageableModels, listManageableProviders, updateProvider } from "../src/api/client";

vi.mock("../src/api/client", () => ({
  updateProvider: vi.fn(),
  listManageableModels: vi.fn(),
  listManageableProviders: vi.fn(),
}));
afterEach(cleanup);
const notify = { success: vi.fn(), error: vi.fn() };

async function show(isAdmin: boolean, hasMyKey: boolean) {
  vi.mocked(listManageableProviders).mockResolvedValue({ items: [{
    provider_key: "deepseek", display_name: "DeepSeek", scope: "global",
    source_type: "system", provider_type: "openai_compatible",
    api_key_env: "", has_request_headers: false, extra_config: {},
    base_url: "https://api.deepseek.com", models_endpoint: null,
    enabled: true, has_api_key: true, has_my_key: hasMyKey,
    effective_has_key: true, enabled_models_count: 0, created_by: "admin",
  }] });
  vi.mocked(listManageableModels).mockResolvedValue({ items: [] });
  render(<AntdApp><ModelSection userId={isAdmin ? "admin" : "member"} isAdmin={isAdmin} notify={notify} /></AntdApp>);
  await screen.findByText("DeepSeek");
}

it("shows the admin's enabled provider and create action", async () => {
  await show(true, false);
  expect(screen.getByText("已启用（1）")).toBeTruthy();
  expect(screen.getByRole("button", { name: "新增供应商" })).toBeTruthy();
});

it("keeps shared providers outside a member's enabled group until a personal key exists", async () => {
  await show(false, false);
  expect(screen.getByText("未启用（1）")).toBeTruthy();
  expect(screen.queryByText("已启用（1）")).toBeNull();
  expect(screen.queryByRole("button", { name: "新增供应商" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "配置供应商：DeepSeek" }));
  expect(await screen.findByText("我的 Key · DeepSeek")).toBeTruthy();
});

it("allows members with personal keys to add models under the shared provider", async () => {
  await show(false, true);
  expect(screen.getByText("已启用（1）")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /管理模型/ }));
  expect(await screen.findByRole("button", { name: "手动添加" })).toBeTruthy();
});


it("edits provider advanced settings and preserves the selected status when saving", async () => {
  await show(true, false);
  vi.mocked(updateProvider).mockResolvedValue({ ok: true });
  fireEvent.click(screen.getByRole("button", { name: "配置供应商：DeepSeek" }));
  expect(await screen.findByText("编辑供应商")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("API Key Env"), { target: { value: "TEST_API_KEY" } });
  fireEvent.click(screen.getByText("高级配置"));
  fireEvent.change(screen.getByLabelText("请求头 JSON"), { target: { value: '{"X-Client":"test"}' } });
  fireEvent.change(screen.getByLabelText("扩展配置 JSON"), { target: { value: '{"enable_thinking":true}' } });
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "思考输出格式" }));
  fireEvent.click(await screen.findByText("独立思考字段（DeepSeek）"));
  fireEvent.click(screen.getByRole("switch", { name: "状态" }));
  fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
  await waitFor(() => expect(updateProvider).toHaveBeenCalledWith("deepseek", expect.objectContaining({
    enabled: false, apiKeyEnv: "TEST_API_KEY", requestHeaders: { "X-Client": "test" },
    extraConfig: { _melonclaw: { reasoning_format: "reasoning_content" }, enable_thinking: true },
  })));
});

it("rejects invalid JSON before saving", async () => {
  await show(true, false);
  vi.mocked(updateProvider).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "配置供应商：DeepSeek" }));
  fireEvent.click(screen.getByText("高级配置"));
  fireEvent.change(screen.getByLabelText("扩展配置 JSON"), { target: { value: '[]' } });
  fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
  expect(updateProvider).not.toHaveBeenCalled();
  expect(notify.error).toHaveBeenCalledWith("扩展配置必须是 JSON 对象");
});


it("keeps one confirm action and saves the selected provider status", async () => {
  await show(true, false);
  vi.mocked(updateProvider).mockClear().mockResolvedValue({ ok: true });
  fireEvent.click(screen.getByRole("button", { name: "配置供应商：DeepSeek" }));
  const status = screen.getByRole("switch", { name: "状态" });
  fireEvent.click(status);
  expect(screen.getAllByRole("button", { name: /确\s*定/ })).toHaveLength(1);
  fireEvent.click(status);
  expect(screen.getAllByRole("button", { name: /确\s*定/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: /确\s*定/ }));
  await waitFor(() => expect(updateProvider).toHaveBeenCalledWith("deepseek", expect.objectContaining({ enabled: true })));
});


it("opens personal key dialogs with an empty input after closing an unsaved draft", async () => {
  await show(false, false);
  const open = () => fireEvent.click(screen.getByRole("button", { name: "配置供应商：DeepSeek" }));
  const input = () => screen.getByPlaceholderText("输入供应商API_Key(只保存不回显)") as HTMLInputElement;
  open();
  expect(input().value).toBe("");
  expect(input().autocomplete).toBe("new-password");
  fireEvent.change(input(), { target: { value: "test-unsaved-key" } });
  fireEvent.click(screen.getByRole("button", { name: "取 消" }));
  open();
  expect(input().value).toBe("");
});


it("discards an admin API key draft when the provider dialog closes", async () => {
  await show(true, false);
  const open = () => fireEvent.click(screen.getByRole("button", { name: "配置供应商：DeepSeek" }));
  open();
  const input = screen.getByLabelText("API Key") as HTMLInputElement;
  expect(input.value).toBe("");
  expect(input.autocomplete).toBe("new-password");
  fireEvent.change(input, { target: { value: "test-unsaved-admin-key" } });
  fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
  open();
  expect((screen.getByLabelText("API Key") as HTMLInputElement).value).toBe("");
});
