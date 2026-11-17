import { App, ConfigProvider } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { knowledgeApi, readKnowledgeDocumentText, type KnowledgeBase, type KnowledgeSearchResult } from "../src/api/knowledge";
import { KnowledgeSearchTest } from "../src/components/KnowledgeSearchTest";

vi.mock("../src/state/session", () => ({ useSession: () => ({ userId: "u", epoch: 1, conversationId: "c" }) }));
vi.mock("../src/api/knowledge", async (load) => {
  const actual = await load<typeof import("../src/api/knowledge")>();
  return { ...actual, knowledgeApi: { ...actual.knowledgeApi, search: vi.fn(), documents: vi.fn() }, readKnowledgeDocumentText: vi.fn() };
});
const kb = "123e4567-e89b-42d3-a456-426614174000";
const doc = "223e4567-e89b-42d3-a456-426614174000";
const rev = "323e4567-e89b-42d3-a456-426614174000";
const base: KnowledgeBase = { knowledge_mode: "vector", active_pipeline_job_id: null, id: kb, name: "资料", description: "", owner_user_id: "u", role: "viewer", share_scope: "selected", status: "active", version: 1, chunk_config: { chunk_size: 800, chunk_overlap: 120 } };
const result: KnowledgeSearchResult = { status: "ok", branches: { sources: { status: "ok", error_code: null, requested: 6, candidates: 1, returned: 1 } }, mode: "vector", items: [{ kind: "source_chunk", chunk_id: "chunk", document_id: doc, revision_id: rev, title: "研报.pdf", content: "<script>不执行</script>\n数字 123", score: 0.8, start_offset: 20, snippet_end_offset: 50, source_locator: { page: 3, method: "ocr" }, source_url: `/api/knowledge-bases/${kb}/documents/${doc}/text?revision_id=${rev}&offset=20&limit=4000` }] };
const show = () => render(<ConfigProvider theme={{ token: { motion: false } }}><App><KnowledgeSearchTest base={base} /></App></ConfigProvider>);
const submit = () => fireEvent.click(screen.getByRole("button", { name: "开始检索" }));
beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} })) });
  vi.mocked(knowledgeApi.documents).mockResolvedValue({ items: [{ id: doc, display_name: "研报.pdf", active_revision_id: rev, source_job: null }, { id: "failed", display_name: "未发布", active_revision_id: null, source_job: null }], total: 21 });
  vi.mocked(knowledgeApi.search).mockResolvedValue(result);
});
it("validates input, submits defaults, displays literal snippets and opens the exact version", async () => {
  show(); submit();
  expect(await screen.findByText("请输入检索问题")).toBeTruthy();
  expect(knowledgeApi.search).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("检索问题"), { target: { value: " 数字是多少 " } }); submit();
  expect(await screen.findByText("1. 研报.pdf")).toBeTruthy();
  expect(knowledgeApi.search).toHaveBeenCalledWith(kb, { query: "数字是多少", mode: "hybrid", top_k: 6, document_ids: [] }, expect.any(AbortSignal));
  expect(screen.getByText("第 3 页")).toBeTruthy();
  expect(screen.getByText(/余弦相似度：0.8000/)).toBeTruthy();
  expect(document.querySelector(".knowledge-search-snippet")?.textContent).toBe(result.items[0].content);
  expect(document.querySelector(".knowledge-search-snippet script")).toBeNull();
  vi.mocked(readKnowledgeDocumentText).mockResolvedValue({ kb_id: kb, document_id: doc, revision_id: rev, title: "原文", content: "版本正文", offset: 20, next_offset: null, total_chars: 100, is_current: true, revision_no: 1 });
  fireEvent.click(screen.getByRole("button", { name: "查看原文" }));
  expect(await screen.findByText("版本正文")).toBeTruthy();
  expect(readKnowledgeDocumentText).toHaveBeenCalledWith(kb, doc, { revisionId: rev, offset: 20, limit: 4000 }, expect.any(AbortSignal));
});
it("loads document pages on demand and sends keyword mode with the selected published document", async () => {
  show();
  expect(knowledgeApi.documents).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "加载文档范围" }));
  await screen.findByRole("button", { name: "加载更多文档" });
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "文档范围" }));
  fireEvent.click(await screen.findByText("研报.pdf"));
  expect(screen.queryByText("未发布")).toBeNull();
  fireEvent.keyDown(screen.getByRole("combobox", { name: "文档范围" }), { key: "Escape" });
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "原文检索方式" }));
  fireEvent.click(await screen.findByText("关键词检索", { selector: ".ant-select-item-option-content" }));
  fireEvent.change(screen.getByLabelText("检索问题"), { target: { value: "收益" } });
  fireEvent.change(screen.getByLabelText("保留条数"), { target: { value: "10" } });
  vi.mocked(knowledgeApi.search).mockResolvedValue({ ...result, items: [], mode: "keyword" }); submit();
  expect(await screen.findByText(/未找到相关片段/)).toBeTruthy();
  expect(knowledgeApi.search).toHaveBeenCalledWith(kb, { query: "收益", mode: "keyword", top_k: 10, document_ids: [doc] }, expect.any(AbortSignal));
  fireEvent.click(screen.getByRole("button", { name: "加载更多文档" }));
  await waitFor(() => expect(knowledgeApi.documents).toHaveBeenLastCalledWith(kb, 2, expect.any(AbortSignal)));
});
it("reports failures without fallback and allows retry with preserved input", async () => {
  show();
  vi.mocked(knowledgeApi.search).mockRejectedValueOnce(new Error("向量检索服务暂不可用"));
  fireEvent.change(screen.getByLabelText("检索问题"), { target: { value: "数字" } }); submit();
  expect(await screen.findByText("向量检索服务暂不可用")).toBeTruthy();
  expect(knowledgeApi.search).toHaveBeenCalledTimes(1);
  expect((screen.getByLabelText("检索问题") as HTMLTextAreaElement).value).toBe("数字");
  submit(); expect(await screen.findByText("1. 研报.pdf")).toBeTruthy();
});
it("aborts both pending requests on close and ignores late responses", async () => {
  let resolveSearch!: (value: KnowledgeSearchResult) => void;
  vi.mocked(knowledgeApi.search).mockImplementation(() => new Promise((resolve) => { resolveSearch = resolve; }));
  vi.mocked(knowledgeApi.documents).mockImplementation(() => new Promise(() => undefined));
  const view = show();
  fireEvent.click(screen.getByRole("button", { name: "加载文档范围" }));
  fireEvent.change(screen.getByLabelText("检索问题"), { target: { value: "数字" } }); submit();
  await waitFor(() => expect(knowledgeApi.search).toHaveBeenCalledTimes(1));
  const searchSignal = vi.mocked(knowledgeApi.search).mock.calls[0][2];
  const documentsSignal = vi.mocked(knowledgeApi.documents).mock.calls[0][2];
  view.unmount();
  expect(searchSignal?.aborted).toBe(true); expect(documentsSignal?.aborted).toBe(true);
  resolveSearch(result);
  expect(screen.queryByText("1. 研报.pdf")).toBeNull();
});


it("supports explicit vector comparison and shows hybrid branch degradation and ranks", async () => {
  show();
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "原文检索方式" }));
  fireEvent.click(await screen.findByText("向量检索", { selector: ".ant-select-item-option-content" }));
  fireEvent.change(screen.getByLabelText("检索问题"), { target: { value: "数字" } }); submit();
  await waitFor(() => expect(knowledgeApi.search).toHaveBeenCalledWith(kb, { query: "数字", mode: "vector", top_k: 6, document_ids: [] }, expect.any(AbortSignal)));

  vi.mocked(knowledgeApi.search).mockResolvedValue({ ...result, mode: "hybrid", status: "degraded",
    notice: "本次向量检索超时，仅完成关键词检索；结果可能不完整。", duration_ms: 123,
    branches: {
      vector: { status: "failed", error_code: "vector_timeout", requested: 6, candidates: 0, returned: 0, duration_ms: 20 },
      keyword: { status: "ok", error_code: null, requested: 6, candidates: 1, returned: 1, duration_ms: 8 },
      fused: { status: "ok", error_code: null, requested: 6, candidates: 1, returned: 1 },
      sources: { status: "ok", error_code: null, requested: 6, candidates: 1, returned: 1 },
    }, items: [{ ...result.items[0], score: 0.0164, rrf_score: 0.0164, keyword_rank: 1, keyword_score: 0.5 }] });
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "原文检索方式" }));
  fireEvent.click(await screen.findByText("混合检索", { selector: ".ant-select-item-option-content" })); submit();
  expect(await screen.findByText(/本次向量检索超时/)).toBeTruthy();
  expect(screen.getByText(/RRF 排序值：0.0164/)).toBeTruthy();
  expect(screen.getByText(/关键词：第 1 名/)).toBeTruthy();
  expect(screen.getByText(/向量：候选 0 \/ 返回 0/)).toBeTruthy();
});

it("shows Office sheet rows, Word blocks and slides without inventing page numbers", async () => {
  vi.mocked(knowledgeApi.search).mockResolvedValue({ ...result, mode: "keyword", items: [
    { ...result.items[0], chunk_id: "sheet", source_locator: { method: "office", kind: "sheet_row", sheet: "收益表", row: 4 } },
    { ...result.items[0], chunk_id: "word", source_locator: { method: "office", kind: "paragraph", block: 3 } },
    { ...result.items[0], chunk_id: "slide", source_locator: { method: "office", kind: "slide", slide: 2 } },
  ] });
  show(); fireEvent.change(screen.getByLabelText("检索问题"), { target: { value: "收益" } }); submit();
  expect(await screen.findByText("工作表：收益表")).toBeTruthy();
  expect(screen.getByText("第 4 行")).toBeTruthy();
  expect(screen.getByText("正文块 3")).toBeTruthy();
  expect(screen.getByText("幻灯片 2")).toBeTruthy();
  expect(screen.queryByText(/第 .* 页/)).toBeNull();
});

