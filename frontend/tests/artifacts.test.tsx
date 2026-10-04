import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ArtifactWorkspace, ArtifactTrigger, MessageArtifactCards } from "../src/components/ArtifactWorkspace";
import { Markdown } from "../src/components/Markdown";
import { ResultProvider } from "../src/components/ResultContext";
import { assetKey, markdownAssetRef } from "../src/lib/resultBlocks";
import type { ChatMessage } from "../src/hooks/useChatStream";
import type { AssetRef } from "../src/lib/resultBlocks";
import type { FileRef } from "../src/lib/workspaceFiles";
import { WorkspaceFileBrowser } from "../src/components/WorkspaceFileBrowser";

const { metadata, blob, index, download, directory, attachments } = vi.hoisted(() => ({ metadata: vi.fn(), blob: vi.fn(), index: vi.fn(), download: vi.fn(), directory: vi.fn(), attachments: vi.fn() }));
vi.mock("../src/api/results", () => ({ resultFileMetadata: metadata, fetchResultBlob: blob, conversationArtifacts: index, workspaceDirectory: directory, workspaceAttachments: attachments,
  resultFileUrl: () => "/download", htmlPreviewUrl: () => "/html-preview", HTML_SOURCE_MARKER: "<!--melonclaw-preview-source-->" }));
vi.mock("../src/lib/resultExport", () => ({ downloadBlob: download }));
const ref = { path: "/outputs/report.html" };
const source = '<html><body><button>示例</button></body></html>';
function message(id: string, artifacts: AssetRef[] = [ref]): ChatMessage {
  return { id, role: "assistant", status: "completed", content: "", markdown: true, events: [], assistantSteps: [], artifacts, phases: [], timestamp: "2026-10-03T00:00:00Z" };
}
function Fixture({ messages = [message("first")], identity = "conversation", sourceMessage = "first" }: { messages?: ChatMessage[]; identity?: string; sourceMessage?: string }) {
  return <ArtifactWorkspace key={identity} userId="owner" conversationId={identity} projectId={null} messages={messages}>
    <div className="chat-view"><ArtifactTrigger /><ResultProvider userId="owner" conversationId={identity} projectId={null} messageId={sourceMessage}>
      <article id={`message-${sourceMessage}`} tabIndex={-1}>
      <Markdown source={`已生成 [报告](/outputs/report.html)\n\n\`\`\`melon-result\n{"version":1,"type":"file","ref":{"path":"/outputs/report.html"}}\n\`\`\``} fileCardsAtEnd deliveredFiles={[ref]} />
      <MessageArtifactCards artifacts={[ref]} />
      </article>
    </ResultProvider></div>
  </ArtifactWorkspace>;
}
beforeEach(() => {
  metadata.mockResolvedValue({ file_name: "report.html", size_bytes: 100, media_type: "text/html", preview_kind: "html" });
  blob.mockResolvedValue({ size: 100, text: async () => "<!doctype html><!--melonclaw-preview-source-->" + source });
  index.mockResolvedValue({ items: [] });
  directory.mockResolvedValue({ path: "/", items: [], next_offset: null, scope: { kind: "conversation", name: "测试" } });
  attachments.mockResolvedValue({ items: [], next_offset: null });
  vi.stubGlobal("URL", class extends URL { static createObjectURL = vi.fn(() => "blob:preview"); static revokeObjectURL = vi.fn(); });
});

describe("workspace file browser", () => {
  const file = (path: string) => ({ name: path.split("/").at(-1), path, kind: "file", size_bytes: 4, modified_at: "2026-10-03T00:00:00Z" });
  const page = (path: string, items: unknown[], next_offset: number | null = null) => ({ path, items, next_offset, total: items.length, scope: { kind: "conversation", name: "测试" } });
  it("browses non-delivered files, returns to their directory and retains delivery provenance", async () => {
    directory.mockImplementation(async (_scope, path) => path === "/" ? page(path, [{ name: "src", path: "/src", kind: "directory", size_bytes: null, modified_at: "" }])
      : page(path, [file("/src/app.py")]));
    metadata.mockImplementation(async (asset: FileRef) => ({ file_name: "workspace_path" in asset ? "app.py" : "report.html", media_type: "text/plain", size_bytes: 4, preview_kind: "text" }));
    blob.mockResolvedValue({ text: async () => "print('example')" });
    render(<Fixture />);
    fireEvent.click(screen.getByRole("button", { name: "查看会话或项目文件" }));
    expect(screen.getByRole("radio", { name: "全部文件" })).toHaveProperty("checked", true);
    fireEvent.click(await screen.findByRole("button", { name: /^src$/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^app.py$/ }));
    expect(await screen.findByText("print('example')")).toBeTruthy();
    expect(metadata).toHaveBeenCalledWith({ workspace_path: "/src/app.py" }, expect.anything(), expect.anything());
    expect(screen.queryByRole("button", { name: "定位到回复" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "文件", exact: true }));
    const row = await within(screen.getByRole("region", { name: "文件列表" })).findByRole("button", { name: /^app.py$/ });
    expect(row.classList.contains("is-selected")).toBe(true);
    expect(screen.queryByLabelText("选择目录")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^src$/ }));
    expect(within(screen.getByRole("region", { name: "文件列表" })).queryByRole("button", { name: /^app.py$/ })).toBeNull();
    expect(await screen.findByRole("button", { name: /^src$/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: /本对话产物/ }));
    fireEvent.click(await within(screen.getByLabelText("文件浏览器")).findByRole("button", { name: "report.html" }));
    fireEvent.click(screen.getByRole("button", { name: "更多文件操作" }));
    expect(await screen.findByRole("menuitem", { name: "定位到回复" })).toBeTruthy();
  });
  it("loads the page containing a file when returning from its message card", async () => {
    directory.mockImplementation(async (_scope, path, options) => path === "/" ? page(path, [{ name: "outputs", path: "/outputs", kind: "directory", size_bytes: null, modified_at: "" }]) : options.offset ? page(path, [file("/outputs/report.html")]) : page(path, [file("/outputs/first.txt")], 200));
    render(<Fixture />);
    fireEvent.click(screen.getByRole("link", { name: "报告" }));
    await screen.findByTitle("report.html");
    fireEvent.click(screen.getByRole("button", { name: "文件", exact: true }));
    const row = await within(screen.getByRole("region", { name: "文件列表" })).findByRole("button", { name: /^report.html$/ });
    await waitFor(() => expect(document.activeElement).toBe(row));
    expect(directory).toHaveBeenCalledWith(expect.anything(), "/outputs", { q: "", sort: "name", offset: 200 }, expect.anything());
    expect(screen.getByRole("button", { name: /^first.txt$/ })).toBeTruthy();
  });
  it("shows attachment original names and returns to the registered attachment list", async () => {
    attachments.mockResolvedValue({ items: [{ attachment_id: "registered-id", file_name: "原始资料.txt", size_bytes: 4, modified_at: "" }], next_offset: null });
    metadata.mockResolvedValue({ file_name: "原始资料.txt", size_bytes: 4, media_type: "text/plain", preview_kind: "text" });
    blob.mockResolvedValue({ text: async () => "附件内容" });
    render(<Fixture />);
    fireEvent.click(screen.getByRole("button", { name: "查看会话或项目文件" }));
    fireEvent.click(screen.getByRole("button", { name: /^上传附件$/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^原始资料.txt$/ }));
    expect(await screen.findByText("附件内容")).toBeTruthy();
    expect(metadata).toHaveBeenCalledWith({ attachment_id: "registered-id" }, expect.anything(), expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "文件", exact: true }));
    expect(await within(screen.getByRole("region", { name: "文件列表" })).findByRole("button", { name: /^原始资料.txt$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^上传附件$/ }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /^上传附件$/ }));
    expect(within(screen.getByRole("region", { name: "文件列表" })).queryByRole("button", { name: /^原始资料.txt$/ })).toBeNull();
  });
  it("paginates the root and ignores late responses after identity changes", async () => {
    const scope = { userId: "owner", conversationId: "conversation", projectId: null };
    const props = { scope, location: "/", rootLabel: "会话文件", selectedKey: "", revision: "", onNavigate: vi.fn(), onOpenFile: vi.fn() };
    directory.mockImplementation(async (_scope, path, options) => page(path, [file(options.offset ? "/second.txt" : "/first.txt")], options.offset ? null : 200));
    const view = render(<WorkspaceFileBrowser {...props} />);
    await screen.findByRole("button", { name: /^first.txt$/ });
    fireEvent.click(screen.getByRole("button", { name: "加载更多文件" }));
    expect(await screen.findByRole("button", { name: /^second.txt$/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^first.txt$/ })).toBeTruthy();
    let resolveOld!: (value: unknown) => void;
    directory.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "刷新文件列表" }));
    const oldSignal = directory.mock.calls.at(-1)![3] as AbortSignal;
    view.rerender(<WorkspaceFileBrowser {...props} scope={{ ...scope, conversationId: "new" }} />);
    expect(oldSignal.aborted).toBe(true);
    resolveOld(page("/", [file("/stale.txt")]));
    await screen.findByRole("button", { name: /^first.txt$/ });
    expect(screen.queryByRole("button", { name: /stale.txt/ })).toBeNull();
  });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

describe("conversation artifact drawer", () => {
  it("preserves list scroll, keeps preview mounted, and restores after closing", async () => {
    directory.mockResolvedValue({ items: [{ name: "report.html", path: "/report.html", kind: "file", size_bytes: 100, modified_at: "" }], next_offset: null });
    const view = render(<Fixture />);
    fireEvent.click(screen.getByRole("button", { name: "查看会话或项目文件" }));
    const list = view.container.querySelector(".workspace-file-list")!;
    list.scrollTop = 120;
    fireEvent.click(await within(screen.getByRole("region", { name: "文件列表" })).findByRole("button", { name: "report.html" }));
    const frame = await screen.findByTitle("report.html");
    const calls = directory.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "文件", exact: true }));

    expect(list.scrollTop).toBe(120);
    expect(frame.isConnected).toBe(true);
    expect(directory).toHaveBeenCalledTimes(calls);
    fireEvent.click(within(screen.getByRole("navigation", { name: "文件视图" })).getByRole("button", { name: "report.html" }));
    expect(screen.getByTitle("report.html")).toBe(frame);
    fireEvent.click(screen.getByRole("button", { name: "关闭文件浏览器" }));
    expect(frame.isConnected).toBe(false);
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查看会话或项目文件" }));
    expect(await screen.findByTitle("report.html")).toBeTruthy();
    expect(screen.getByRole("region", { name: "当前文件" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭当前文件" }));
    expect(screen.queryByRole("region", { name: "当前文件" })).toBeNull();
    expect(screen.queryByLabelText("选择目录")).toBeNull();
  });
  it("does not poll hidden directories and retains loaded pages when shown again", async () => {
    const props = { scope: { userId: "owner", conversationId: "conversation", projectId: null }, location: "/", rootLabel: "文件", selectedKey: "", revision: "", onNavigate: vi.fn(), onOpenFile: vi.fn() };
    directory.mockImplementation(async (_scope, _path, options) => ({ items: [{ name: options.offset ? "second.txt" : "first.txt", path: options.offset ? "/second.txt" : "/first.txt", kind: "file", size_bytes: 1, modified_at: "" }], next_offset: options.offset ? null : 200 }));
    const view = render(<WorkspaceFileBrowser {...props} />);
    await screen.findByRole("button", { name: "first.txt" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多文件" }));
    await screen.findByRole("button", { name: "second.txt" });
    view.rerender(<WorkspaceFileBrowser {...props} active={false} />);
    const calls = directory.mock.calls.length;
    vi.useFakeTimers();
    await act(async () => { vi.advanceTimersByTime(15000); });
    vi.useRealTimers();
    expect(directory).toHaveBeenCalledTimes(calls);
    view.rerender(<WorkspaceFileBrowser {...props} />);
    expect(screen.getByRole("button", { name: "second.txt" })).toBeTruthy();
    expect(directory).toHaveBeenCalledTimes(calls);
  });
  it("normalizes encoded Markdown refs without accepting traversal or remote URLs", () => {
    expect(markdownAssetRef("/outputs/%E6%8A%A5%E5%91%8A.html")).toEqual({ path: "/outputs/报告.html" });
    expect(markdownAssetRef("/outputs/%2e%2e/private")).toBeNull();
    expect(markdownAssetRef("https://evil.test/report.html")).toBeNull();
    expect(markdownAssetRef("javascript:alert(1)")).toBeNull();
    expect(assetKey({ attachment_id: "ABC" })).toBe("/attachments/abc");
  });
  it("hoists a file result once and opens the same preview from link or card", async () => {
    const view = render(<Fixture />);
    expect(await screen.findByRole("button", { name: "查看 report.html" })).toBeTruthy();
    await waitFor(() => expect(view.container.querySelector(".markdown-pre code")).toBeNull());
    expect(view.container.querySelectorAll(".artifact-file-card")).toHaveLength(1);
    expect(view.container.querySelector(".artifact-drawer")).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "报告" }));
    const frame = await screen.findByTitle("report.html");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("allow")).toContain("camera 'none'");
    expect(frame.getAttribute("src")).toBe("blob:preview");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    fireEvent.click(screen.getByRole("button", { name: "查看 report.html" }));
    expect(screen.getByTitle("report.html")).toBe(frame);
    fireEvent.click(screen.getByRole("button", { name: "更多文件操作" }));
    expect(await screen.findByRole("menuitem", { name: "text/html · 100 B" })).toBeTruthy();
    expect(blob).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "关闭文件浏览器" }));
    fireEvent.click(screen.getByRole("button", { name: "查看 report.html" }));
    expect(await screen.findByTitle("report.html")).toBeTruthy();
  });
  it("keeps the iframe mounted across tabs and later deliveries; only explicit refresh reloads", async () => {
    const view = render(<Fixture />);
    fireEvent.click(screen.getByRole("link", { name: "报告" }));
    const frame = await screen.findByTitle("report.html");
    fireEvent.click(screen.getByRole("radio", { name: "源码" }));
    expect(await screen.findByText(source)).toBeTruthy();
    expect(frame.isConnected).toBe(true);
    expect(frame.hasAttribute("hidden")).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "预览" }));
    expect(screen.getByTitle("report.html")).toBe(frame);
    view.rerender(<Fixture messages={[message("first"), message("second")]} />);
    expect(await screen.findByText(/此文件已重新交付/)).toBeTruthy();
    expect(screen.getByTitle("report.html")).toBe(frame);
    expect(blob).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "刷新查看" }));
    await waitFor(() => expect(blob).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/此文件已重新交付/)).toBeNull();
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
  it("includes files from unloaded history, merges duplicate paths, and resets on conversation change", async () => {
    index.mockResolvedValue({ items: [{ ref, message_id: "old" }, { ref: { path: "/outputs/old.txt" }, message_id: "unloaded", created_at: "2026-10-01T00:00:00Z" }] });
    metadata.mockImplementation(async (asset: AssetRef) => ({ file_name: "path" in asset ? asset.path.split("/").at(-1) : "附件", media_type: "text/plain", size_bytes: 4, preview_kind: "text" }));
    const view = render(<Fixture messages={[message("first"), message("second")]} />);
    fireEvent.click(screen.getByRole("button", { name: "查看会话或项目文件" }));
    await waitFor(() => expect(screen.getByLabelText("文件范围").textContent).toContain("本对话产物 · 2"));
    fireEvent.click(screen.getByRole("radio", { name: /本对话产物/ }));
    const panel = screen.getByLabelText("文件浏览器");
    expect(await within(panel).findByRole("button", { name: "old.txt" })).toBeTruthy();
    expect(within(panel).getAllByRole("button", { name: "report.html" })).toHaveLength(1);
    view.rerender(<Fixture identity="another" />);
    expect(screen.queryByRole("complementary", { name: "文件浏览器" })).toBeNull();
  });
  it("shows preview errors, retries, and downloads current bytes without opening the drawer", async () => {
    render(<Fixture />);
    const card = await screen.findByRole("button", { name: "查看 report.html" });
    const current = { size: 100, text: async () => source };
    blob.mockResolvedValueOnce(current);
    fireEvent.click(screen.getByRole("button", { name: "下载" }));
    await waitFor(() => expect(download).toHaveBeenCalledWith(current, "report.html"));
    expect(screen.queryByRole("complementary", { name: "文件浏览器" })).toBeNull();
    blob.mockRejectedValueOnce(new Error("文件不存在"));
    fireEvent.click(card);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "文件不存在");
    fireEvent.click(screen.getByRole("button", { name: "更多文件操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "刷新文件" }));
    expect(await screen.findByTitle("report.html")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("complementary", { name: "文件浏览器" })).toBeNull();
  });
  it("closes an expanded panel before locating and focusing the delivered reply", async () => {
    vi.stubGlobal("innerWidth", 1440);
    const view = render(<Fixture />);
    const article = view.container.querySelector<HTMLElement>("#message-first")!;
    article.scrollIntoView = vi.fn();
    fireEvent.click(screen.getByRole("link", { name: "报告" }));
    await screen.findByTitle("report.html");
    fireEvent.click(screen.getByRole("button", { name: "放大文件面板" }));
    expect(view.container.querySelector(".chat-view")?.hasAttribute("inert")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "更多文件操作" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "定位到回复" }));
    await waitFor(() => expect(article.scrollIntoView).toHaveBeenCalled());
    expect(screen.queryByRole("complementary", { name: "文件浏览器" })).toBeNull();
    expect(view.container.querySelector(".chat-view")?.hasAttribute("inert")).toBe(false);
    expect(document.activeElement).toBe(article);
  });
});
