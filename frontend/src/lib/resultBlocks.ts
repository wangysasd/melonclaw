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
class ResultValidationError extends Error {}

function parseResultValue(raw: string): ResultBlock | null {
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
        const invalid = (message: string): never => { throw new ResultValidationError(message); };
        if (!keys(value, ["version", "type", "title", "chart", "unit", "x_label", "series", "rows", "sources", "note"])) invalid("图表包含不支持的字段。");
        for (const [field, limit] of [["title", 300], ["unit", 100], ["x_label", 100]] as const) {
          if (!text(value[field], limit)) invalid(`图表 ${field} 必须是非空字符串，且不超过 ${limit} 字符。`);
        }
        if (value.chart !== "line" && value.chart !== "bar") invalid("图表类型不支持，chart 只允许 line 或 bar。");
        if (value.sources === undefined) invalid("图表缺少必填字段 sources，暂时无法展示。");
        if (!sources(value.sources)) invalid("图表 sources 必须包含 1–30 条有效来源，每条需有唯一 id 和非空 title；链接与文件引用也必须有效。");
        if (value.note !== undefined && !text(value.note)) invalid("图表 note 必须是非空字符串，且不超过 6000 字符。");
        const series = value.series;
        if (!Array.isArray(series) || !series.length || series.length > 8 || !series.every((name) => text(name, 100)) || new Set(series).size !== series.length) return invalid("图表 series 必须包含 1–8 个不重复的非空系列名称，每个不超过 100 字符。");
        const rows = value.rows;
        if (!Array.isArray(rows) || !rows.length || rows.length > 200) return invalid("图表 rows 必须包含 1–200 行数据。");
        const width = series.length + 1;
        for (const [index, row] of rows.entries()) {
          if (!Array.isArray(row) || row.length !== width) return invalid(`图表第 ${index + 1} 行列数不匹配，应有 ${width} 列（横轴标签及各系列数值）。`);
          if (!text(row[0], 100)) invalid(`图表第 ${index + 1} 行横轴标签必须是非空字符串，且不超过 100 字符。`);
          if (!row.slice(1).every((number) => number === null || (typeof number === "number" && Number.isFinite(number)))) invalid(`图表第 ${index + 1} 行数值类型错误，系列值必须是有限数值或 null，不能使用字符串数字。`);
        }
        return value as ResultBlock;
      }
      default: return null;
    }
  } catch (error) {
    if (error instanceof ResultValidationError) throw error;
    return null;
  }
}

export type ResultParseOutcome = { result: ResultBlock; error: null } | { result: null; error: string };

export function parseResultBlockDetailed(raw: string): ResultParseOutcome {
  try {
    const result = parseResultValue(raw);
    return result ? { result, error: null } : { result: null, error: "结果格式无法识别。" };
  } catch (error) {
    if (error instanceof ResultValidationError) return { result: null, error: error.message };
    throw error;
  }
}

export function parseResultBlock(raw: string): ResultBlock | null {
  return parseResultBlockDetailed(raw).result;
}
