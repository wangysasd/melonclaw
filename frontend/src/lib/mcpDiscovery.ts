import type { McpToolDiscoveryResult } from "../types/api";

export function discoveryStatus(result: McpToolDiscoveryResult): "unknown" | "abnormal" | "warning" | "normal" {
  if (!result.ok) return result.error_code === "busy" ? "unknown" : "abnormal";
  return result.missing_allowed_tools.length || !result.enabled_tool_count ? "warning" : "normal";
}
