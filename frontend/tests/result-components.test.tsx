import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Markdown } from "../src/components/Markdown";
import ResultBlockView from "../src/components/ResultBlockView";
import { ResultProvider } from "../src/components/ResultContext";
import { ResultAsset } from "../src/components/ResultAsset";
import { TextDiff } from "../src/components/TextDiff";
import { parseResultBlock } from "../src/lib/resultBlocks";
import { tableCsv } from "../src/lib/resultExport";
import { textDiff } from "../src/lib/textDiff";
import { ArtifactWorkspace } from "../src/components/ArtifactWorkspace";

vi.mock("../src/components/ResultChartCanvas", () => ({ default: ({ chart }: { chart: { rows: unknown[][] } }) => <div data-testid="chart">{JSON.stringify(chart.rows)}</div> }));
const { metadata, blob, copy } = vi.hoisted(() => ({ metadata: vi.fn(), blob: vi.fn(), copy: vi.fn().mockResolvedValue(undefined) }));
vi.mock("../src/api/results", () => ({ resultFileMetadata: metadata, resultFileUrl: () => "/checked-result", fetchResultBlob: blob, conversationArtifacts: async () => ({ items: [] }) }));
vi.mock("../src/lib/clipboard", () => ({ copyText: copy }));
beforeEach(() => { copy.mockResolvedValue(undefined); });
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
const chart = { version: 1, type: "chart", title: "销售额", chart: "line", unit: "万元", x_label: "月份", series: ["销售"], rows: [["一月", 12], ["二月", null]], sources: [{ id: "1", title: "演示数据", url: "https://example.com/data" }], note: "模拟数据" };
const block = (value: unknown) => `\`\`\`melon-result\n${JSON.stringify(value)}\n\`\`\``;

describe("explicit result contract", () => {
  it("validates rectangular numeric data and rejects unsafe refs/options/oversize", () => {
    expect(parseResultBlock(JSON.stringify(chart))).not.toBeNull();
    for (const invalid of [{ ...chart, chart: "pie" }, { ...chart, rows: [["一月", "12"]] }, { ...chart, rows: [["一月", 12, 3]] }, { ...chart, option: { formatter: "script" } }, { ...chart, sources: [{ id: "1", title: "x", url: "javascript:alert(1)" }] }, { version: 1, type: "file", ref: { path: "/outputs/../private" } }, { version: 1, type: "image", ref: { url: "https://evil.com/a.png" } }]) expect(parseResultBlock(JSON.stringify(invalid))).toBeNull();
    expect(parseResultBlock("x".repeat(200001))).toBeNull();
  });
  it.each([
    [{ sources: undefined }, "缺少必填字段 sources"],
    [{ sources: [] }, "1–30 条有效来源"],
    [{ chart: "pie" }, "图表类型不支持"],
    [{ rows: [["一月", 12, 8]] }, "第 1 行列数不匹配"],
    [{ rows: [["一月", "12"]] }, "第 1 行数值类型错误"],
    [{ option: {} }, "不支持的字段"],
  ])("explains invalid chart fields while preserving raw data: %j", (change, reason) => {
    const raw = JSON.stringify({ ...chart, ...change });
    const { container } = render(<ResultBlockView raw={raw} />);
    expect(screen.getByRole("status").textContent).toContain(reason);
    expect(container.querySelector("code")?.textContent).toBe(raw);
    expect(screen.queryByTestId("chart")).toBeNull();
  });
  it("renders the reported two-series example only after a source is supplied", async () => {
    const example = { version: 1, type: "chart", title: "示例：月度销量（模拟数据）", chart: "bar", unit: "万台", x_label: "月份", series: ["A 产品", "B 产品"], rows: [["1月", 12, 8], ["2月", 15, 9], ["3月", 11, 14], ["4月", 18, 16]], note: "以上为模拟数据，仅用于演示图表渲染效果，不代表任何真实统计口径。" };
    const view = render(<Markdown source={block(example)} />);
    expect(await screen.findByText(/缺少必填字段 sources/)).toBeTruthy();
    view.rerender(<Markdown source={block({ ...example, sources: [{ id: "1", title: "模拟数据，仅用于演示" }] })} />);
    expect((await screen.findByTestId("chart")).textContent).toBe(JSON.stringify(example.rows));
    expect(screen.getByText("查看绘图数据（4 行）")).toBeTruthy();
    expect(screen.getByText("[1] 模拟数据，仅用于演示")).toBeTruthy();
    fireEvent.click(screen.getByText("查看绘图数据（4 行）"));
    fireEvent.click(screen.getByRole("button", { name: "复制表格" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith("月份\tA 产品（万台）\tB 产品（万台）\n1月\t12\t8\n2月\t15\t9\n3月\t11\t14\n4月\t18\t16"));
  });
  it("explains unescaped source title quotes and preserves the original content", () => {
    const raw = '{"version":1,"type":"sources","items":[{"id":"2","title":"储能电芯"扩产竞赛"降温"}]}';
    const view = render(<ResultBlockView raw={raw} />);
    expect(screen.getByRole("status").textContent).toContain("JSON 语法错误");
    expect(view.container.querySelector("code")?.textContent).toBe(raw);
    expect(parseResultBlock(JSON.stringify({ version: 1, type: "sources", items: [{ id: "2", title: '储能电芯"扩产竞赛"降温' }] }))).not.toBeNull();
  });
  it("downloads exact plotted values, escaping CSV and spreadsheet formulas", () => {
    expect(tableCsv([["项目", "值"], ["=cmd()", -2], ['a,"b\nc', null], ["  +SUM(1)", true]])).toBe('"项目","值"\r\n"\'=cmd()","-2"\r\n"a,""b\nc",""\r\n"\'  +SUM(1)","true"');
  });
  it("preserves invalid raw result as readable text without executable HTML", () => {
    const raw = '{"version":1,"type":"chart","title":"<script>alert(1)</script>"}';
    const { container } = render(<ResultBlockView raw={raw} />);
    expect(screen.getByRole("status").textContent).toContain("图表 unit 必须是非空字符串");
    expect(container.textContent).toContain(raw);
    expect(container.querySelector("script")).toBeNull();
  });
});

describe("result interactions inside real Markdown", () => {
  it("renders completed explicit chart, collapsed matching table, units and sources", async () => {
    render(<Markdown source={block(chart)} />);
    const renderedChart = await screen.findByTestId("chart");
    expect(renderedChart.closest("pre")).toBeNull();
    expect(screen.getByText("横轴：月份 · 单位：万元")).toBeTruthy();
    const summary = screen.getByText("查看绘图数据（2 行）");
    expect(summary.closest("details")?.open).toBe(false);
    fireEvent.click(summary);
    expect(screen.getByRole("cell", { name: "12" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "—" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "复制表格" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith("月份\t销售（万元）\n一月\t12\n二月\t"));
  });
  it("keeps an unfinished streaming result readable until its fence closes", async () => {
    const raw = `\`\`\`melon-result\n${JSON.stringify(chart)}`;
    const view = render(<Markdown source={raw} streaming />);
    expect(screen.queryByTestId("chart")).toBeNull();
    expect(view.container.textContent).toContain("销售额");
    view.rerender(<Markdown source={`${raw}\n\`\`\``} streaming />);
    expect(await screen.findByTestId("chart")).toBeTruthy();
  });
  it("adds copy/download controls to existing Markdown table", async () => {
    render(<Markdown source={"| 名称 | 数量 |\n| --- | --- |\n| A | 2 |"} />);
    fireEvent.click(screen.getByRole("button", { name: "复制表格" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith("名称\t数量\nA\t2"));
    expect(screen.getByRole("button", { name: "下载 CSV" })).toBeTruthy();
  });
  it("uses message-local citation anchors and expands source on click", async () => {
    render(<ResultProvider userId="owner" conversationId="conversation" projectId={null}><Markdown source={`结论[1](#source-1)\n\n${block({ version: 1, type: "sources", items: [{ id: "1", title: "报告", url: "https://example.com/report", quote: "引用片段" }] })}`} /></ResultProvider>);
    const source = await screen.findByText("[1] 报告");
    const details = source.closest("details")!;
    details.scrollIntoView = vi.fn();
    const link = screen.getByRole("link", { name: "1" });
    expect(link.getAttribute("href")).toBe(`#${details.id}`);
    fireEvent.click(link);
    expect(details.open).toBe(true);
    expect(details.scrollIntoView).toHaveBeenCalled();
  });
  it("never loads an external image and accepts explicit owned image references", async () => {
    metadata.mockResolvedValue({ file_name: "plot.png", size_bytes: 123, media_type: "image/png", preview_kind: "image" });
    const { container } = render(<ResultProvider userId="owner" conversationId="conversation" projectId={null}><Markdown source="![外部](https://example.com/a.png)\n\n![图](/outputs/%E5%9B%BE.png)" /></ResultProvider>);
    expect(container.querySelector('img[src="https://example.com/a.png"]')).toBeNull();
    expect(await screen.findByAltText("图")).toBeTruthy();
    expect(metadata.mock.calls[0][0]).toEqual({ path: "/outputs/图.png" });
    fireEvent.error(screen.getByAltText("图"));
    expect(await screen.findByText(/图片加载失败/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新加载" })).toBeTruthy();
  });
});

describe("asset and diff boundaries", () => {
  it("shows authoritative metadata, readable text preview and download errors", async () => {
    const computedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) => computedStyle(element));
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: false, addListener: vi.fn(), removeListener: vi.fn() }));
    vi.stubGlobal("URL", class extends URL { static createObjectURL = vi.fn(() => "blob:test"); static revokeObjectURL = vi.fn(); });
    metadata.mockResolvedValue({ file_name: "实际名称.txt", size_bytes: 4, media_type: "text/plain", preview_kind: "text" });
    blob.mockResolvedValue({ size: 4, text: async () => "<script>plain text</script>" });
    render(<ArtifactWorkspace userId="owner" conversationId="conversation" projectId={null} messages={[]}><ResultProvider userId="owner" conversationId="conversation" projectId={null}><ResultAsset asset={{ path: "/outputs/result.txt" }} /></ResultProvider></ArtifactWorkspace>);
    expect(await screen.findByText("实际名称.txt")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "查看" }));
    expect(await screen.findByText("<script>plain text</script>")).toBeTruthy();
    expect(document.querySelector("script")).toBeNull();
    blob.mockRejectedValue(new Error("文件不可访问"));
    fireEvent.click(screen.getAllByRole("button", { name: "下载" })[0]);
    expect(await screen.findByText("文件不可访问")).toBeTruthy();
  });
  it("keeps unchanged lines and highlights added/removed lines, collapses long diffs", () => {
    expect(textDiff("same\nold\nend", "same\nnew\nend").lines).toEqual([{ kind: "same", text: "same" }, { kind: "removed", text: "old" }, { kind: "added", text: "new" }, { kind: "same", text: "end" }]);
    const { container } = render(<TextDiff before="same\nold" after="same\nnew" />);
    expect(container.querySelector(".diff-added")?.textContent).toContain("new");
    expect(container.querySelector(".diff-removed")?.textContent).toContain("old");
    const long = render(<TextDiff before={Array.from({ length: 30 }, (_, i) => `line${i}`).join("\n")} after="new" />);
    expect(long.container.querySelector("details")?.open).toBe(false);
  });
});
