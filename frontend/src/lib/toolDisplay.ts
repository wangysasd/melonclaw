import { toolDefinition, toolIconName } from "./toolCatalog";

export function toolSummary(name: string): string {
  return toolDefinition(name)?.label || (name === "write_todos" ? "更新任务清单" : name) || "未知工具";
}

export { toolIconName };

/** 优先用这些字段做一行摘要：它们通常就是用户最关心的定位信息。 */
const SUMMARY_KEYS = [
  "command",
  "query",
  "path",
  "file_path",
  "filepath",
  "pattern",
  "url",
  "skill_id",
  "name",
  "id",
];

const SUMMARY_LIMIT = 120;

function firstScalar(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    for (const item of value) {
      const text = firstScalar(item);
      if (text) return text;
    }
    return null;
  }
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of SUMMARY_KEYS) {
      if (key in record) {
        const text = firstScalar(record[key]);
        if (text) return text;
      }
    }
    for (const item of Object.values(record)) {
      const text = firstScalar(item);
      if (text) return text;
    }
  }
  return null;
}

/**
 * 工具调用的一行摘要：参数是 JSON 时取最有代表性的标量，
 * 否则退回参数预览的第一行。只做展示裁剪，不改动原始数据。
 */
export function toolCallSummary(argsPreview: string | null | undefined): string | null {
  const raw = (argsPreview ?? "").trim();
  if (!raw) return null;
  let text: string | null = null;
  if (raw.startsWith("{") || raw.startsWith("[")) {
    try {
      text = firstScalar(JSON.parse(raw));
    } catch {
      text = null;
    }
  }
  if (!text) {
    text = raw.split("\n").map((line) => line.trim()).find(Boolean) ?? null;
  }
  if (!text) return null;
  const compact = text.replace(/\s+/g, " ");
  return compact.length > SUMMARY_LIMIT
    ? `${compact.slice(0, SUMMARY_LIMIT)}…`
    : compact;
}
