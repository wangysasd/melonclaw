import { App, ConfigProvider } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { KnowledgeManager } from "../src/components/KnowledgeManager";
import { knowledgeApi, type KnowledgeBase } from "../src/api/knowledge";
import { listManageableModels } from "../src/api/client";
import { antdTheme } from "../src/theme/antd";

// 真正的 Ant Design 表单/表格在全量并行运行时需要更长的交互时限。
vi.setConfig({ testTimeout: 15000 });

const context = vi.hoisted(() => ({ userId: "owner", users: [{ user_id: "owner", tenant_role: "member" }] }));
vi.mock("../src/state/session", () => ({ useSession: () => context }));
vi.mock("../src/api/knowledge", async (load) => ({ ...await load<typeof import("../src/api/knowledge")>(), knowledgeApi: {
  list: vi.fn(), detail: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(),
  documents: vi.fn(),
  members: vi.fn(), candidates: vi.fn(), userCandidates: vi.fn(), setShareScope: vi.fn(), saveMember: vi.fn(), revoke: vi.fn(), transfer: vi.fn(),
} }));
vi.mock("../src/api/client", async (load) => ({ ...await load<typeof import("../src/api/client")>(), listManageableModels: vi.fn(), listManageableProviders: vi.fn().mockResolvedValue({ items: [] }) }));
const base: KnowledgeBase = { knowledge_mode: "vector", active_pipeline_job_id: null, id: "kb-test", owner_user_id: "owner", name: "投研资料", description: "研究资料", status: "active", version: 3, role: "owner", share_scope: "selected", chunk_config: { chunk_size: 800, chunk_overlap: 120 } };
const show = () => render(<ConfigProvider theme={{ ...antdTheme, token: { ...antdTheme.token, motion: false } }}><App><KnowledgeManager onClose={vi.fn()} /></App></ConfigProvider>);
const choose = async (name: string, label: string) => {
  fireEvent.mouseDown(screen.getByRole("combobox", { name }));
  fireEvent.click(await screen.findByText(label, { selector: ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content" }));
};
beforeEach(() => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) });
  vi.clearAllMocks();
  context.userId = "owner"; context.users = [{ user_id: "owner", tenant_role: "member" }];
  vi.mocked(knowledgeApi.list).mockResolvedValue({ items: [base], total: 1 });
  vi.mocked(knowledgeApi.detail).mockResolvedValue({ ...base });
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [], total: 0, active_pipeline_job_id: null });
  vi.mocked(knowledgeApi.create).mockResolvedValue({ ...base });
  vi.mocked(knowledgeApi.update).mockResolvedValue({ ...base, version: 4 });
  vi.mocked(knowledgeApi.members).mockResolvedValue({ items: [{ user_id: "viewer", user_name_zh: "李四", tenant_name_zh: "研究组", role: "viewer" }] });
  vi.mocked(knowledgeApi.candidates).mockResolvedValue({ items: [{ user_id: "target", user_name_zh: "张三", tenant_name_zh: "研究组" }] });
  vi.mocked(knowledgeApi.userCandidates).mockResolvedValue({ items: [{ user_id: "target", user_name_zh: "张三", tenant_name_zh: "研究组" }] });
  vi.mocked(listManageableModels).mockResolvedValue({ items: [] });
});
afterEach(cleanup);

it("shows system administrators upload and edit, with deletion in the actions menu", async () => {
  vi.mocked(knowledgeApi.list).mockResolvedValue({ items: [{ ...base, role: "system_admin" }], total: 1 });
  show();
  expect(await screen.findByText("系统管理员")).toBeTruthy();
  expect(screen.getByRole("button", { name: /编\s*辑/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /上\s*传/ })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "删除知识库" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "更多操作" }));
  expect(await screen.findByRole("menuitem", { name: "删除知识库" })).toBeTruthy();
});

it("selects named users when creating a restricted library", async () => {
  show(); await screen.findByText("投研资料");
  fireEvent.click(screen.getByRole("button", { name: "创建知识库" }));
  fireEvent.change(await screen.findByLabelText("名称"), { target: { value: "指定资料" } });
  fireEvent.click(screen.getByRole("radio", { name: "指定人" }));
  await waitFor(() => expect(knowledgeApi.userCandidates).toHaveBeenCalled());
  await choose("指定查看人员", "张三（target）· 研究组");
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(knowledgeApi.create).toHaveBeenCalledWith({ name: "指定资料", description: "", share_scope: "selected", selected_user_ids: ["target"], chunk_config: { chunk_size: 800, chunk_overlap: 120 } }));
});

it("creates through the real form, validates chunk overlap without asking for embedding configuration", async () => {
  show(); await screen.findByText("投研资料");
  fireEvent.click(screen.getByRole("button", { name: "创建知识库" }));
  expect(await screen.findByLabelText("名称")).toBeTruthy();
  expect(screen.queryByLabelText("嵌入模型")).toBeNull();
  expect(screen.queryByText("去配置")).toBeNull();
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新研究库" } });
  fireEvent.change(screen.getByLabelText("重叠字符"), { target: { value: "200" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  expect(await screen.findByText("重叠不能超过分块大小的 20%")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("重叠字符"), { target: { value: "120" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(knowledgeApi.create).toHaveBeenCalledWith({ name: "新研究库", description: "", share_scope: "tenant", chunk_config: { chunk_size: 800, chunk_overlap: 120 } }));
  expect(listManageableModels).not.toHaveBeenCalled();
});

it("edits with the current version and never sends a replacement model binding", async () => {
  show(); await screen.findByText("投研资料");
  fireEvent.click(screen.getByRole("button", { name: /编\s*辑/ }));
  expect(await screen.findByLabelText("名称")).toBeTruthy();
  expect(screen.queryByLabelText("文档解析模型（可选）")).toBeNull();
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新名称" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(knowledgeApi.update).toHaveBeenCalledWith("kb-test", { name: "新名称", description: "研究资料", share_scope: "selected", selected_user_ids: ["viewer"], chunk_config: base.chunk_config, version: 3 }));
});

async function openMembers() {
  show(); await screen.findByText("投研资料");
  fireEvent.click(screen.getByRole("button", { name: /成\s*员/ }));
  await screen.findByText("李四");
}
it("saves a drafted range explicitly and confirms narrowing to named people", async () => {
  vi.mocked(knowledgeApi.setShareScope).mockResolvedValue({ ...base, share_scope: "everyone", version: 4 });
  await openMembers();
  fireEvent.click(screen.getByRole("radio", { name: "所有人" }));
  expect(knowledgeApi.setShareScope).not.toHaveBeenCalled();
  expect(screen.getByRole("status").textContent).toContain("当前生效：指定人");
  fireEvent.click(screen.getByRole("button", { name: "保存范围" }));
  await waitFor(() => expect(screen.getByRole("status").textContent).toBe("当前生效：所有人"));
  expect(knowledgeApi.setShareScope).toHaveBeenCalledWith("kb-test", "everyone", 3);
  fireEvent.click(screen.getByRole("radio", { name: "指定人" }));
  fireEvent.click(screen.getByRole("button", { name: "保存范围" }));
  expect(knowledgeApi.setShareScope).toHaveBeenCalledTimes(1);
  fireEvent.click(await screen.findByRole("button", { name: "确认切换" }));
  await waitFor(() => expect(knowledgeApi.setShareScope).toHaveBeenCalledWith("kb-test", "selected", 4));
});
it("grants a named user with the selected role", async () => {
  await openMembers();
  await choose("选择成员", "张三（target）· 研究组");
  await choose("授权角色", "编辑者");
  fireEvent.click(screen.getByRole("button", { name: /授\s*权/ }));
  await waitFor(() => expect(knowledgeApi.saveMember).toHaveBeenCalledWith("kb-test", "target", "editor"));
}, 15000);
it("adjusts an existing member role", async () => {
  await openMembers();
  await choose("李四的角色", "编辑者");
  await waitFor(() => expect(knowledgeApi.saveMember).toHaveBeenCalledWith("kb-test", "viewer", "editor"));
}, 15000);
it("revokes only after confirmation", async () => {
  await openMembers();
  fireEvent.click(screen.getByRole("button", { name: /撤\s*权/ }));
  expect(knowledgeApi.revoke).not.toHaveBeenCalled();
  expect(await screen.findByText(/默认查看范围仍覆盖此人/)).toBeTruthy();
  fireEvent.click(await screen.findByRole("button", { name: "确认撤权" }));
  await waitFor(() => expect(knowledgeApi.revoke).toHaveBeenCalledWith("kb-test", "viewer"));
}, 15000);
it("transfers ownership after confirmation with the current version", async () => {
  await openMembers();
  fireEvent.click(screen.getByText("所有权设置"));
  await choose("新所有者", "张三（target）· 研究组");
  fireEvent.click(screen.getByRole("button", { name: /转\s*让/ }));
  expect(knowledgeApi.transfer).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "确认转让" }));
  await waitFor(() => expect(knowledgeApi.transfer).toHaveBeenCalledWith("kb-test", "target", 3));
}, 15000);

it("opens the document drawer without opening the file picker", async () => {
  const editable = { ...base, role: "editor" } as const;
  vi.mocked(knowledgeApi.list).mockResolvedValue({ items: [editable], total: 1 });
  vi.mocked(knowledgeApi.detail).mockResolvedValue(editable);
  show();
  const upload = await screen.findByRole("button", { name: /上\s*传/ });
  const picker = vi.spyOn(HTMLInputElement.prototype, "click");
  try {
    fireEvent.click(upload);
    const uploadDocument = await screen.findByRole("button", { name: "上传文档" });
    expect(picker).not.toHaveBeenCalled();
    fireEvent.click(uploadDocument);
    expect(picker.mock.instances).toContain(screen.getByLabelText("选择文档文件"));
  } finally {
    picker.mockRestore();
  }
  expect(screen.queryByRole("button", { name: "更多操作" })).toBeNull();
});

it("offers only reading to a viewer and shows the document empty state", async () => {
  vi.mocked(knowledgeApi.list).mockResolvedValue({ items: [{ ...base, role: "viewer" }], total: 1 });
  vi.mocked(knowledgeApi.detail).mockResolvedValue({ ...base, role: "viewer" });
  show(); await screen.findByText("投研资料");
  expect(screen.queryByRole("button", { name: /成\s*员/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /编\s*辑/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /删\s*除/ })).toBeNull();
  expect(screen.queryByRole("tab", { name: "嵌入模型" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "投研资料" }));
  expect(await screen.findByText("暂无文档")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /上传/ })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "库信息" }));
  expect(await screen.findByText("800 字符")).toBeTruthy();
});

it("keeps all knowledge model configuration hidden even for administrators", async () => {
  context.users = [{ user_id: "owner", tenant_role: "admin" }];
  show(); await screen.findByText("投研资料");
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByText("文档解析模型")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "创建知识库" }));
  expect(await screen.findByLabelText("名称")).toBeTruthy();
  expect(screen.queryByLabelText("文档解析模型（可选）")).toBeNull();
  expect(listManageableModels).not.toHaveBeenCalled();
});


it("opens retrieval testing from the detail tab for a viewer", async () => {
  vi.mocked(knowledgeApi.detail).mockResolvedValue({ ...base, role: "viewer" });
  show(); await screen.findByText("投研资料");
  fireEvent.click(screen.getByRole("button", { name: "投研资料" }));
  fireEvent.click(await screen.findByRole("tab", { name: "检索测试" }));
  expect(await screen.findByLabelText("检索问题")).toBeTruthy();
  expect(screen.getByRole("button", { name: "开始检索" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "文档管理" })).toBeNull();
});

it("creates a vector library with chunk settings", async () => {
  show(); await screen.findByText("投研资料");
  fireEvent.click(screen.getByRole("button", { name: "创建知识库" }));
  expect(screen.queryByLabelText("知识库类型")).toBeNull();
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "资料库" } });
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(knowledgeApi.create).toHaveBeenCalledWith({ name: "资料库", description: "", share_scope: "tenant", chunk_config: { chunk_size: 800, chunk_overlap: 120 } }));
});
