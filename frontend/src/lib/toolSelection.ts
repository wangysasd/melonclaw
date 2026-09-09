const TOOL_SELECTOR_PREFIX = '{"tools"';
const MAX_SELECTOR_CANDIDATE_LENGTH = 200;

export type ToolSelectorTextKind = "text" | "pending" | "selector";

/** 识别动态工具选择器的内部 JSON，避免把实现细节当作助手正文展示。 */
export function classifyToolSelectorText(value: string): ToolSelectorTextKind {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith("{")) return "text";

  const mightBeSelector =
    TOOL_SELECTOR_PREFIX.startsWith(trimmed) ||
    trimmed.startsWith(TOOL_SELECTOR_PREFIX);
  if (!mightBeSelector) return "text";

  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "tools" in parsed &&
      Array.isArray(parsed.tools)
    ) {
      return "selector";
    }
    return "text";
  } catch {
    return trimmed.length <= MAX_SELECTOR_CANDIDATE_LENGTH ? "pending" : "text";
  }
}
