import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readKnowledgeDocumentText, type KnowledgeDocumentText } from "../src/api/knowledge";
import { ApiError } from "../src/api/client";
import { KnowledgeCitationProvider, KnowledgeSourceAllow } from "../src/components/KnowledgeCitationProvider";
import { messageKnowledgeSources } from "../src/lib/knowledgeSources";
import { Markdown } from "../src/components/Markdown";

const session = { epoch: 1, userId: "u1", conversationId: "c1" };
vi.mock("../src/state/session", () => ({ useSession: () => session }));
vi.mock("../src/api/knowledge", async (load) => {
  const actual = await load<typeof import("../src/api/knowledge")>();
  return { ...actual, readKnowledgeDocumentText: vi.fn() };
});

const kbId = "123e4567-e89b-42d3-a456-426614174000";
const documentId = "223e4567-e89b-42d3-a456-426614174000";
const revisionId = "323e4567-e89b-42d3-a456-426614174000";
const href = `/api/knowledge-bases/${kbId}/documents/${documentId}/text?revision_id=${revisionId}&offset=0&limit=4000`;

function page(overrides: Partial<KnowledgeDocumentText> = {}): KnowledgeDocumentText {
  return {
    kb_id: kbId, document_id: documentId, revision_id: revisionId,
    title: "研究资料.txt", content: "历史原文 📚", offset: 0, next_offset: 10,
    total_chars: 20, is_current: false, revision_no: 2, ...overrides,
  };
}

function renderCitation(source: string) {
  return render(<KnowledgeCitationProvider><Markdown source={source} /></KnowledgeCitationProvider>);
}

beforeEach(() => vi.mocked(readKnowledgeDocumentText).mockReset());
afterEach(() => vi.restoreAllMocks());

describe("knowledge source citations", () => {
  it("opens same-origin text citations in the drawer and pages with server offsets", async () => {
    vi.mocked(readKnowledgeDocumentText)
      .mockResolvedValueOnce(page())
      .mockResolvedValueOnce(page({ content: "后续文本", offset: 10, next_offset: null }));
    renderCitation(`[查看原文](${href})`);
    fireEvent.click(screen.getByRole("link", { name: "查看原文" }));

    expect(await screen.findByText("研究资料.txt")).toBeTruthy();
    expect(screen.getByText("历史版本 · 第 2 版")).toBeTruthy();
    expect(screen.getByText("历史原文 📚")).toBeTruthy();
    expect(readKnowledgeDocumentText).toHaveBeenCalledWith(kbId, documentId,
      { revisionId, offset: 0, limit: 4000 }, expect.any(AbortSignal));

    fireEvent.click(screen.getByRole("button", { name: "继续查看原文" }));
    await waitFor(() => expect(screen.getByText("历史原文 📚后续文本")).toBeTruthy());
    expect(readKnowledgeDocumentText).toHaveBeenLastCalledWith(kbId, documentId,
      { revisionId, offset: 10, limit: 4000 }, expect.any(AbortSignal));
    expect(document.querySelector(".knowledge-source-text")?.tagName).toBe("PRE");
  });

  it("opens expanded citations of up to 8000 characters", async () => {
    vi.mocked(readKnowledgeDocumentText).mockResolvedValue(page());
    renderCitation(`[查看上下文](${href.replace("limit=4000", "limit=8000")})`);
    fireEvent.click(screen.getByRole("link", { name: "查看上下文" }));
    expect(await screen.findByText("历史原文 📚")).toBeTruthy();
    expect(readKnowledgeDocumentText).toHaveBeenCalledWith(kbId, documentId,
      { revisionId, offset: 0, limit: 8000 }, expect.any(AbortSignal));
  });

  it("does not navigate directly to JSON and reports missing or expired references", async () => {
    vi.mocked(readKnowledgeDocumentText).mockRejectedValue(new ApiError(404, "not found"));
    renderCitation(`[查看旧引用](${href})`);
    fireEvent.click(screen.getByRole("link", { name: "查看旧引用" }));
    expect(await screen.findByText(/原引用版本不可用/)).toBeTruthy();
    expect(document.querySelector(".knowledge-source-text")).toBeNull();
  });

  it("aborts a pending page request when the user changes conversation", async () => {
    let pageSignal: AbortSignal | undefined;
    vi.mocked(readKnowledgeDocumentText)
      .mockResolvedValueOnce(page())
      .mockImplementationOnce((_kb, _document, _input, signal) => {
        pageSignal = signal;
        return new Promise<KnowledgeDocumentText>(() => undefined);
      });
    const view = renderCitation(`[查看原文](${href})`);
    fireEvent.click(screen.getByRole("link", { name: "查看原文" }));
    await screen.findByText("研究资料.txt");
    fireEvent.click(screen.getByRole("button", { name: "继续查看原文" }));
    await waitFor(() => expect(readKnowledgeDocumentText).toHaveBeenCalledTimes(2));

    session.conversationId = "c2";
    view.rerender(<KnowledgeCitationProvider><Markdown source={`[查看原文](${href})`} /></KnowledgeCitationProvider>);
    await waitFor(() => expect(pageSignal?.aborted).toBe(true));
    session.conversationId = "c1";
  });

  it("leaves external links outside the citation drawer", () => {
    renderCitation("[外部页面](https://example.com/document)");
    const link = screen.getByRole("link", { name: "外部页面" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.queryByText("知识库原文")).toBeNull();
    expect(readKnowledgeDocumentText).not.toHaveBeenCalled();
  });

  it("does not open a source drawer for answers with only #source-* anchors", () => {
    renderCitation("ERC-20 与 ERC-721 用途不同 [1](#source-1)\n\n".repeat(8));
    fireEvent.click(screen.getAllByRole("link", { name: "1" })[0]);
    expect(readKnowledgeDocumentText).not.toHaveBeenCalled();
    expect(screen.queryByText("知识库原文")).toBeNull();
  });

  it("opens the drawer from a versioned link even when the answer also has anchors", async () => {
    vi.mocked(readKnowledgeDocumentText).mockResolvedValueOnce(page());
    renderCitation(`赎回按面值处理 [1](#source-1)，参见 [查看原文](${href})`);
    fireEvent.click(screen.getByRole("link", { name: "查看原文" }));
    expect(await screen.findByText("研究资料.txt")).toBeTruthy();
    expect(readKnowledgeDocumentText).toHaveBeenCalledWith(kbId, documentId,
      { revisionId, offset: 0, limit: 4000 }, expect.any(AbortSignal));
  });
});

it("opens only knowledge links that belong to this answer's tool results", async () => {
  const message = { assistantSteps: [{ id: "s", ordinal: 1, content: "", status: "completed" as const, is_final: true, tool_calls: [{ call_id: "c", name: "search_knowledge", batch_index: 0, status: "completed" as const, result_preview: "展示预览已截断", knowledge_sources: [{ title: "研报", kind: "source_chunk", source_url: href, listed: true }] }] }], events: [] };
  const sources = messageKnowledgeSources(message, window.location.origin);
  expect(sources.enforce).toBe(true);
  expect(sources.listed.map((item) => item.title)).toEqual(["研报"]);
  vi.mocked(readKnowledgeDocumentText).mockResolvedValue(page());
  render(<KnowledgeCitationProvider><KnowledgeSourceAllow allow={sources.keys}><Markdown source={`[研报](${href}) [其他](/api/knowledge-bases/${kbId}/documents/${revisionId}/text?revision_id=${documentId}&offset=0&limit=4000)`} /></KnowledgeSourceAllow></KnowledgeCitationProvider>);
  fireEvent.click(screen.getByRole("link", { name: "研报" }));
  expect(await screen.findByText("研究资料.txt")).toBeTruthy();
  fireEvent.click(screen.getByRole("link", { name: "其他" }));
  expect(await screen.findByText("此链接不在本次检索结果中，未打开。")).toBeTruthy();
  expect(readKnowledgeDocumentText).toHaveBeenCalledTimes(1);
});

it.each([6, 20])("keeps all %i structured citations independently of truncated previews", async (count) => {
  const cards = Array.from({ length: count }, (_, index) => ({ title: `来源${index}`, source_url: href.replace("offset=0", `offset=${index}`) }));
  const sources = messageKnowledgeSources({ assistantSteps: [{ id: "s", ordinal: 1, content: "", status: "completed", is_final: false,
    tool_calls: [{ call_id: "c", name: "search_knowledge", batch_index: 0, status: "completed", result_preview: "不完整 JSON…", knowledge_sources: cards }] }] }, window.location.origin);
  expect(sources.keys?.size).toBe(count);
  expect(sources.listed).toHaveLength(count);
  vi.mocked(readKnowledgeDocumentText).mockResolvedValue(page());
  render(<KnowledgeCitationProvider><KnowledgeSourceAllow allow={sources.keys}><Markdown source={`[末尾来源](${cards[count - 1].source_url})`} /></KnowledgeSourceAllow></KnowledgeCitationProvider>);
  fireEvent.click(screen.getByRole("link", { name: "末尾来源" }));
  await waitFor(() => expect(readKnowledgeDocumentText).toHaveBeenCalledWith(kbId, documentId,
    { revisionId, offset: count - 1, limit: 4000 }, expect.any(AbortSignal)));
});

it("keeps subagent evidence links without trusting links embedded in preview text", () => {
  const evidenceUrl = href.replace("offset=0", "offset=123");
  const sources = messageKnowledgeSources({ assistantSteps: [], events: [{ type: "subagent_tool_result", name: "open_knowledge_document",
    content: JSON.stringify({ knowledge_sources: [{ source_url: href }] }),
    knowledge_sources: [{ title: "原文", source_url: evidenceUrl }, { source_url: href, listed: false }],
  }] }, window.location.origin);
  expect(sources.keys?.size).toBe(2);
  expect(sources.listed.map((item) => item.source_url)).toEqual([evidenceUrl]);
  const empty = messageKnowledgeSources({ assistantSteps: [], events: [{ type: "subagent_tool_result", name: "search_knowledge", content: JSON.stringify({ knowledge_sources: [{ source_url: href }] }) }] }, window.location.origin);
  expect(empty.enforce).toBe(true);
  expect(empty.keys?.size).toBe(0);
});

