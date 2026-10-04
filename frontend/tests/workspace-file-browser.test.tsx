import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceFileBrowser } from "../src/components/WorkspaceFileBrowser";

const { directory, attachments } = vi.hoisted(() => ({ directory: vi.fn(), attachments: vi.fn() }));
vi.mock("../src/api/results", () => ({ workspaceDirectory: directory, workspaceAttachments: attachments }));
const file = (path: string) => ({ name: path.split("/").at(-1), path, kind: "file", size_bytes: 4, modified_at: "" });
const folder = (path: string) => ({ name: path.split("/").at(-1), path, kind: "directory", size_bytes: null, modified_at: "" });
const attachment = (attachment_id: string, file_name: string) => ({ attachment_id, file_name, size_bytes: 4, modified_at: "" });
const page = (items: unknown[], next_offset: number | null = null) => ({ items, next_offset });
const props = { scope: { userId: "owner", conversationId: "conversation", projectId: null }, location: "/", selectedKey: "", revision: "", onOpenFile: vi.fn() };

beforeEach(() => {
  directory.mockReset().mockResolvedValue(page([]));
  attachments.mockReset().mockResolvedValue(page([]));
  props.onOpenFile.mockReset();
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("unified workspace root", () => {
  it("shows uploads and outputs immediately, retains real folders and other workspace files", async () => {
    directory.mockImplementation(async (_scope, path) => path === "/" ? page([folder("/outputs"), file("/notes.txt"), folder("/images")])
      : path === "/outputs" ? page([file("/outputs/bee.html"), folder("/outputs/charts")])
      : page([file("/outputs/charts/chart.svg")]));
    attachments.mockResolvedValue(page([attachment("upload-id", "brief.pdf")]));
    const view = render(<WorkspaceFileBrowser {...props} />);
    const output = await screen.findByRole("button", { name: "bee.html 输出" });
    const upload = screen.getByRole("button", { name: "brief.pdf 上传" });
    expect(screen.queryByRole("button", { name: "上传附件" })).toBeNull();
    expect(screen.queryByRole("button", { name: "outputs" })).toBeNull();
    expect(screen.getByRole("button", { name: "notes.txt" })).toBeTruthy();
    expect(directory.mock.calls.map((call) => call[1])).toEqual(["/", "/outputs"]);
    expect([...view.container.querySelectorAll(".workspace-file-row")].map((row) => row.textContent)).toEqual(["charts输出", "images", "bee.html输出", "brief.pdf上传", "notes.txt"]);
    fireEvent.click(output);
    expect(props.onOpenFile).toHaveBeenLastCalledWith({ path: "/outputs/bee.html" });
    fireEvent.click(upload);
    expect(props.onOpenFile).toHaveBeenLastCalledWith({ attachment_id: "upload-id" });
    fireEvent.click(screen.getByRole("button", { name: "charts 输出" }));
    fireEvent.click(await screen.findByRole("button", { name: "chart.svg 输出" }));
    expect(props.onOpenFile).toHaveBeenLastCalledWith({ path: "/outputs/charts/chart.svg" });
    fireEvent.click(screen.getByRole("button", { name: "charts 输出" }));
    expect(screen.queryByRole("button", { name: "chart.svg 输出" })).toBeNull();
  });

  it("keeps identically named files and folders separate by their actual references", async () => {
    directory.mockImplementation(async (_scope, path) => path === "/" ? page([folder("/outputs"), folder("/data"), file("/report.pdf")])
      : path === "/outputs" ? page([folder("/outputs/data"), file("/outputs/report.pdf")]) : page([file(`${path}/one.txt`)]));
    attachments.mockResolvedValue(page([attachment("first", "report.pdf"), attachment("second", "report.pdf")]));
    const view = render(<WorkspaceFileBrowser {...props} />);
    await screen.findByRole("button", { name: "report.pdf 输出" });
    for (const row of screen.getAllByRole("button", { name: /^report.pdf/ })) fireEvent.click(row);
    expect(props.onOpenFile.mock.calls.map(([ref]) => ref)).toEqual(expect.arrayContaining([
      { workspace_path: "/report.pdf" }, { path: "/outputs/report.pdf" }, { attachment_id: "first" }, { attachment_id: "second" },
    ]));
    expect(props.onOpenFile).toHaveBeenCalledTimes(4);
    fireEvent.click(screen.getByRole("button", { name: "data", exact: true }));
    fireEvent.click(screen.getByRole("button", { name: "data 输出" }));
    await screen.findByRole("button", { name: "one.txt 输出" });
    expect(view.container.querySelectorAll(".workspace-file-tree-node")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "one.txt", exact: true })).toBeTruthy();
  });

  it("locates an uploaded file on a later page without scanning unrelated sources", async () => {
    attachments.mockImplementation(async (_scope, options) => options.offset ? page([attachment("target", "target.txt")]) : page([attachment("first", "first.txt")], 200));
    render(<WorkspaceFileBrowser {...props} location="attachments" selectedKey="/attachments/target" />);
    const row = await screen.findByRole("button", { name: "target.txt 上传" });
    await waitFor(() => expect(document.activeElement).toBe(row));
    expect(row.classList.contains("is-selected")).toBe(true);
    expect(attachments).toHaveBeenCalledWith(expect.anything(), { q: "", sort: "name", offset: 200 }, expect.anything());
    expect(directory).toHaveBeenCalledTimes(1);
  });

  it("discovers outputs beyond the root first page and unfolds the selected real subfolder", async () => {
    directory.mockImplementation(async (_scope, path, options) => path === "/"
      ? options.offset ? page([folder("/outputs")]) : page([folder("/aaa")], 200)
      : path === "/outputs" ? page([folder("/outputs/charts")]) : page([file("/outputs/charts/plot.svg")]));
    render(<WorkspaceFileBrowser {...props} location="/outputs/charts" selectedKey="/outputs/charts/plot.svg" />);
    const row = await screen.findByRole("button", { name: "plot.svg 输出" });
    await waitFor(() => expect(document.activeElement).toBe(row));
    expect(screen.getByRole("button", { name: "charts 输出" }).getAttribute("aria-expanded")).toBe("true");
    expect(directory.mock.calls.map((call) => call[1])).not.toContain("/aaa");
  });

  it("paginates each source, retains loaded pages while hidden and refreshes all sources", async () => {
    directory.mockImplementation(async (_scope, path, options) => path === "/" ? page([folder("/outputs")])
      : options.offset ? page([file("/outputs/second.txt")]) : page([file("/outputs/first.txt")], 200));
    attachments.mockImplementation(async (_scope, options) => options.offset ? page([attachment("second", "second.pdf")]) : page([attachment("first", "first.pdf")], 200));
    const view = render(<WorkspaceFileBrowser {...props} />);
    await screen.findByRole("button", { name: "first.txt 输出" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多文件" }));
    await screen.findByRole("button", { name: "second.txt 输出" });
    expect(screen.getByRole("button", { name: "second.pdf 上传" })).toBeTruthy();
    const list = view.container.querySelector(".workspace-file-list")!;
    list.scrollTop = 120;
    view.rerender(<WorkspaceFileBrowser {...props} active={false} />);
    const calls = [directory.mock.calls.length, attachments.mock.calls.length];
    vi.useFakeTimers();
    await act(async () => { vi.advanceTimersByTime(15000); });
    vi.useRealTimers();
    view.rerender(<WorkspaceFileBrowser {...props} />);
    expect([directory.mock.calls.length, attachments.mock.calls.length]).toEqual(calls);
    expect(list.scrollTop).toBe(120);
    expect(screen.getByRole("button", { name: "second.txt 输出" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "刷新文件列表" }));
    await screen.findByRole("button", { name: "first.txt 输出" });
    expect(screen.queryByRole("button", { name: "second.txt 输出" })).toBeNull();
    expect(screen.queryByRole("button", { name: "second.pdf 上传" })).toBeNull();
    expect(directory.mock.calls.filter((call) => call[1] === "/outputs" && call[2].offset === 0)).toHaveLength(2);
  });

  it("retains expanded folder pages across preview switches and aborts pending child requests", async () => {
    directory.mockImplementation(async (_scope, path, options) => path === "/" ? page([folder("/outputs")])
      : path === "/outputs" ? page([folder("/outputs/charts")])
      : options.offset ? page([file("/outputs/charts/second.svg")], 400) : page([file("/outputs/charts/first.svg")], 200));
    const view = render(<WorkspaceFileBrowser {...props} />);
    fireEvent.click(await screen.findByRole("button", { name: "charts 输出" }));
    await screen.findByRole("button", { name: "first.svg 输出" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多文件" }));
    await screen.findByRole("button", { name: "second.svg 输出" });
    view.rerender(<WorkspaceFileBrowser {...props} active={false} />);
    view.rerender(<WorkspaceFileBrowser {...props} />);
    expect(screen.getByRole("button", { name: "second.svg 输出" })).toBeTruthy();
    directory.mockImplementationOnce(() => new Promise(() => {}));
    fireEvent.click(screen.getByRole("button", { name: "加载更多文件" }));
    const signal = directory.mock.calls.at(-1)![3] as AbortSignal;
    view.rerender(<WorkspaceFileBrowser {...props} active={false} />);
    expect(signal.aborted).toBe(true);
  });

  it("keeps successful sources visible when outputs fail and retries the failed page", async () => {
    directory.mockImplementation(async (_scope, path, options) => {
      if (path === "/") return page([folder("/outputs"), file("/notes.txt")]);
      if (options.offset) throw new Error("暂时无法读取");
      return page([file("/outputs/first.txt")], 200);
    });
    attachments.mockResolvedValue(page([attachment("brief", "brief.pdf")]));
    render(<WorkspaceFileBrowser {...props} />);
    await screen.findByRole("button", { name: "first.txt 输出" });
    fireEvent.click(screen.getByRole("button", { name: "加载更多文件" }));
    const error = await screen.findByRole("alert");
    expect(error.textContent).toContain("输出文件加载失败");
    expect(screen.getByRole("button", { name: "notes.txt" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "brief.pdf 上传" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "first.txt 输出" })).toBeTruthy();
    directory.mockImplementation(async (_scope, path) => page([file(`${path}/second.txt`)]));
    fireEvent.click(within(error).getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("button", { name: "second.txt 输出" })).toBeTruthy();
    expect(directory.mock.calls.at(-1)![2].offset).toBe(200);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "first.txt 输出" })).toBeTruthy();
  });

  it("shows uploads when the root fails, and displays one empty state when all sources are empty", async () => {
    directory.mockRejectedValue(new Error("工作区暂不可读"));
    attachments.mockResolvedValue(page([attachment("brief", "brief.pdf")]));
    render(<WorkspaceFileBrowser {...props} />);
    expect(await screen.findByRole("button", { name: "brief.pdf 上传" })).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toContain("工作区文件加载失败");
    cleanup();
    directory.mockResolvedValue(page([]));
    attachments.mockResolvedValue(page([]));
    render(<WorkspaceFileBrowser {...props} />);
    expect(await screen.findByText("当前还没有文件。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(directory.mock.calls.some((call) => call[1] === "/outputs")).toBe(false);
  });

  it("loads a newly created outputs directory on the next visible poll", async () => {
    let created = false;
    directory.mockImplementation(async (_scope, path) => page(path === "/" ? created ? [folder("/outputs")] : [] : [file("/outputs/new.txt")]));
    vi.useFakeTimers();
    render(<WorkspaceFileBrowser {...props} />);
    await act(async () => {});
    expect(screen.getByText("当前还没有文件。")).toBeTruthy();
    created = true;
    await act(async () => { vi.advanceTimersByTime(5000); });
    expect(screen.getByRole("button", { name: "new.txt 输出" })).toBeTruthy();
    expect(directory.mock.calls.map((call) => call[1])).toContain("/outputs");
  });

  it("starts independent sources together, aborts hidden requests and ignores late identity responses", async () => {
    let resolveRoot!: (value: unknown) => void;
    let resolveAttachments!: (value: unknown) => void;
    directory.mockImplementationOnce(() => new Promise((resolve) => { resolveRoot = resolve; }));
    attachments.mockImplementationOnce(() => new Promise((resolve) => { resolveAttachments = resolve; }));
    const view = render(<WorkspaceFileBrowser {...props} />);
    expect(directory).toHaveBeenCalledTimes(1);
    expect(attachments).toHaveBeenCalledTimes(1);
    const oldSignals = [directory.mock.calls[0][3], attachments.mock.calls[0][2]] as AbortSignal[];
    view.rerender(<WorkspaceFileBrowser {...props} active={false} />);
    expect(oldSignals.every((signal) => signal.aborted)).toBe(true);
    view.rerender(<WorkspaceFileBrowser {...props} scope={{ ...props.scope, conversationId: "new" }} />);
    await screen.findByText("当前还没有文件。");
    await act(async () => {
      resolveRoot(page([folder("/outputs"), file("/stale.txt")]));
      resolveAttachments(page([attachment("stale", "stale.pdf")]));
    });
    expect(screen.queryByRole("button", { name: /stale/ })).toBeNull();
    expect(directory.mock.calls.some((call) => call[1] === "/outputs")).toBe(false);
  });
});
