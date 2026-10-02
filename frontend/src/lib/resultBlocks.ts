/** melon-result v1：显式结果数据，绝不接收可执行代码或图表 option。 */
export type AssetRef = { attachment_id: string } | { path: string };
export type ResultSource = { id: string; title: string; url?: string; quote?: string; ref?: AssetRef; locator?: string };
export type Cell = string | number | boolean | null;
export type ResultBlock =
  | { version: 1; type: "chart"; title: string; chart: "line" | "bar"; unit: string; x_label: string; series: string[]; rows: (string | number | null)[][]; sources: ResultSource[]; note?: string }
  | { version: 1; type: "table"; title: string; columns: string[]; rows: Cell[][] }
  | { version: 1; type: "image" | "file"; ref: AssetRef; caption?: string }
  | { version: 1; type: "diff"; file_name: string; before: string; after: string }
  | { version: 1; type: "sources"; items: ResultSource[] };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function text(value: unknown, limit = 6000): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= limit;
}
function keys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}
export function safeExternalUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > 2000 || /[\s\\]/.test(value)) return undefined;
  try {
    const parsed = new URL(value);
    return ["https:", "http:"].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : undefined;
  } catch { return undefined; }
}
export function parseAssetRef(value: unknown): AssetRef | null {
  if (!record(value)) return null;
  if (keys(value, ["attachment_id"]) && typeof value.attachment_id === "string" && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value.attachment_id)) return { attachment_id: value.attachment_id };
  if (keys(value, ["path"]) && typeof value.path === "string" && value.path.length <= 1000 && value.path.startsWith("/outputs/") && !value.path.includes("\\") && ![...value.path].some((char) => char.charCodeAt(0) < 32) && value.path.slice(1).split("/").every((part) => part && !part.startsWith("."))) return { path: value.path };
  return null;
}
function sources(value: unknown): value is ResultSource[] {
  return Array.isArray(value) && value.length > 0 && value.length <= 30 && value.every((item) => record(item)
    && keys(item, ["id", "title", "url", "quote", "ref", "locator"])
    && typeof item.id === "string" && /^[A-Za-z0-9_-]{1,40}$/.test(item.id) && text(item.title, 300)
    && (item.url === undefined || Boolean(safeExternalUrl(item.url)))
    && (item.quote === undefined || text(item.quote))
    && (item.locator === undefined || text(item.locator, 300))
    && (item.ref === undefined || Boolean(parseAssetRef(item.ref))))
    && new Set(value.map((item) => item.id)).size === value.length;
}
function cell(value: unknown): value is Cell {
  return value === null || typeof value === "boolean" || (typeof value === "string" && value.length <= 6000) || (typeof value === "number" && Number.isFinite(value));
}
export function parseResultBlock(raw: string): ResultBlock | null {
  if (raw.length > 200_000) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!record(value) || value.version !== 1) return null;
    switch (value.type) {
      case "image": case "file":
        return keys(value, ["version", "type", "ref", "caption"]) && parseAssetRef(value.ref) && (value.caption === undefined || text(value.caption)) ? value as ResultBlock : null;
      case "diff":
        return keys(value, ["version", "type", "file_name", "before", "after"]) && text(value.file_name, 300) && typeof value.before === "string" && typeof value.after === "string" && value.before.length + value.after.length <= 120_000 ? value as ResultBlock : null;
      case "sources":
        return keys(value, ["version", "type", "items"]) && sources(value.items) ? value as ResultBlock : null;
      case "table": {
        if (!keys(value, ["version", "type", "title", "columns", "rows"]) || !text(value.title, 300) || !Array.isArray(value.columns) || value.columns.length < 1 || value.columns.length > 30 || !value.columns.every((column) => text(column, 100))) return null;
        const width = value.columns.length;
        return Array.isArray(value.rows) && value.rows.length <= 1000 && value.rows.every((row) => Array.isArray(row) && row.length === width && row.every(cell)) ? value as ResultBlock : null;
      }
      case "chart": {
        if (!keys(value, ["version", "type", "title", "chart", "unit", "x_label", "series", "rows", "sources", "note"]) || !text(value.title, 300) || !["line", "bar"].includes(String(value.chart)) || !text(value.unit, 100) || !text(value.x_label, 100) || !sources(value.sources) || (value.note !== undefined && !text(value.note))) return null;
        if (!Array.isArray(value.series) || !value.series.length || value.series.length > 8 || !value.series.every((name) => text(name, 100)) || new Set(value.series).size !== value.series.length) return null;
        const width = value.series.length + 1;
        return Array.isArray(value.rows) && value.rows.length > 0 && value.rows.length <= 200 && value.rows.every((row) => Array.isArray(row) && row.length === width && text(row[0], 100) && row.slice(1).every((number) => number === null || (typeof number === "number" && Number.isFinite(number)))) ? value as ResultBlock : null;
      }
      default: return null;
    }
  } catch { return null; }
}
