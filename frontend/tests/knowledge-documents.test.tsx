import { App, ConfigProvider } from "antd";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { KnowledgeDocuments } from "../src/components/KnowledgeDocuments";
import { knowledgeApi, type KnowledgeBase } from "../src/api/knowledge";
import { antdTheme } from "../src/theme/antd";
vi.setConfig({ testTimeout: 15000 });
vi.mock("../src/api/knowledge", async (load) => ({ ...await load<typeof import("../src/api/knowledge")>(), knowledgeApi: {
  documents: vi.fn(), upload: vi.fn(), documentAction: vi.fn(), download: vi.fn(),
} }));
const base: KnowledgeBase = { knowledge_mode: "vector", active_pipeline_job_id: null, id: "kb", owner_user_id: "owner", name: "测试库", description: "", status: "active", version: 1, role: "editor", share_scope: "selected", chunk_config: { chunk_size: 800, chunk_overlap: 120 } };
const show = (role: KnowledgeBase["role"] = "editor") => render(<ConfigProvider theme={{ ...antdTheme, token: { ...antdTheme.token, motion: false } }}><App><KnowledgeDocuments base={{ ...base, role }} /></App></ConfigProvider>);
beforeEach(() => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) });
  vi.clearAllMocks();
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(knowledgeApi.upload).mockResolvedValue({ status: "queued" });
});
afterEach(cleanup);
it("uploads a selected real file and retains the same request on a network retry", async () => {
  vi.mocked(knowledgeApi.upload).mockRejectedValueOnce(new Error("连接中断"));
  show(); await screen.findByText("暂无文档");
  const file = new File(["中文资料"], "research.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("选择文档文件"), { target: { files: [file] } });
  await screen.findByText("连接中断");
  const first = vi.mocked(knowledgeApi.upload).mock.calls[0];
  fireEvent.click(screen.getByRole("button", { name: "重试上传" }));
  await waitFor(() => expect(knowledgeApi.upload).toHaveBeenCalledTimes(2));
  expect(vi.mocked(knowledgeApi.upload).mock.calls[1]).toEqual(first);
  expect(first[0]).toBe("kb"); expect(first[1]).toBe(file);
});
it("shows current stage and permits cancellation by an editor", async () => {
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [{ id: "doc", display_name: "demo.pdf", active_revision_id: "old", source_job: { id: "job", status: "running", stage: "embedding", error_summary: null } }], total: 1 });
  show(); await screen.findByText("新版本 · 处理中 · 向量化");
  expect(screen.getByText("可检索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "取消处理" }));
  await waitFor(() => expect(knowledgeApi.documentAction).toHaveBeenCalledWith("kb", "doc", "cancel"));
});
it("shows failure guidance in the row while keeping replace and retry and requiring deletion confirmation", async () => {
  const guidance = "文件有打开密码。请解除密码后替换文件。";
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [{ id: "doc", display_name: "demo.pdf", active_revision_id: "old", source_job: { id: "job", operation: "upload", status: "failed", stage: "parsing", error_code: "ocr_password", error_summary: guidance } }], total: 1 });
  show();
  const row = within(await screen.findByRole("row", { name: /demo\.pdf/ }));
  expect(row.getByText(guidance)).toBeTruthy();
  expect(row.getByRole("button", { name: /替\s*换/ })).toBeTruthy();
  fireEvent.click(row.getByRole("button", { name: "重试原文" }));
  await waitFor(() => expect(knowledgeApi.documentAction).toHaveBeenCalledWith("kb", "doc", "retry", expect.any(String)));
  vi.mocked(knowledgeApi.documentAction).mockClear();
  fireEvent.click(screen.getByRole("button", { name: /删\s*除/ }));
  expect(knowledgeApi.documentAction).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "确认删除" }));
  await waitFor(() => expect(knowledgeApi.documentAction).toHaveBeenCalledWith("kb", "doc", "delete"));
});
it("offers a viewer only download of the published original", async () => {
  const document = { id: "doc", display_name: "demo.pdf", active_revision_id: "old", source_job: null };
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [document], total: 1 });
  show("viewer"); await screen.findByText("demo.pdf");
  expect(screen.queryByRole("button", { name: "上传文档" })).toBeNull();
  expect(screen.queryByRole("button", { name: "替换" })).toBeNull();
  expect(screen.queryByRole("button", { name: /删\s*除/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "下载原件" }));
  await waitFor(() => expect(knowledgeApi.download).toHaveBeenCalledWith("kb", document));
});


it("lists the supported extensions by file type", async () => {
  show(); await screen.findByText("暂无文档");
  const input = screen.getByLabelText("选择文档文件");
  expect(input.getAttribute("accept")?.split(",").sort()).toEqual(
    ".pdf,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.txt,.md".split(",").sort(),
  );
  fireEvent.click(screen.getByRole("button", { name: "支持的格式" }));
  const table = within(await screen.findByRole("table", { name: "支持的文档格式" }));
  expect(table.getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["类型", "扩展名"]);
  expect(within(table.getByRole("row", { name: /^纯文本/ })).getByRole("cell").textContent).toBe(".txt、.md");
  expect(within(table.getByRole("row", { name: /^PDF、Word、PowerPoint/ })).getByRole("cell").textContent)
    .toBe(".pdf、.doc、.docx、.ppt、.pptx");
  expect(within(table.getByRole("row", { name: /^图片/ })).getByRole("cell").textContent)
    .toBe(".png、.jpg、.jpeg、.webp、.gif、.bmp、.tif、.tiff");
  const file = new File(["office-container"], "研究.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(knowledgeApi.upload).toHaveBeenCalledWith("kb", file, expect.any(String), undefined));
});

it("blocks new uploads and replacements while the server retains the pipeline slot", async () => {
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [{ id: "doc", display_name: "资料.txt", active_revision_id: "rev", source_job: null }], total: 1, active_pipeline_job_id: "pipeline" });
  show(); await screen.findByText("资料.txt");
  expect((screen.getByRole("button", { name: "上传文档" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: /替\s*换/ }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "下载原件" }) as HTMLButtonElement).disabled).toBe(false);
});
