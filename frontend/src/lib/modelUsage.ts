import type { DisplayEvent } from "../types/api";

export const USAGE_LABELS: Record<string, string> = {
  main: "主模型", selection: "工具选择", summary: "摘要", subagent: "子 Agent",
};

export function mergeDisplayEvent(events: DisplayEvent[], event: DisplayEvent): DisplayEvent[] {
  const key = event.type === "model_usage" ? "call_id" : event.type === "context_usage" ? "scope" : null;
  if (!key) return [...events, event];
  const index = events.findIndex((item) => item.type === event.type && item[key] === event[key]);
  if (index < 0) return [...events, event];
  return events.map((item, i) => i === index ? { ...item, ...event } : item);
}

export function summarizeModelUsage(events: DisplayEvent[]) {
  // 同一调用的开始与结束只计一次；审批恢复的历史也按 call_id 合并。
  const calls = new Map<string, DisplayEvent>();
  for (const event of events) {
    if (event.type === "model_usage" && typeof event.call_id === "string") calls.set(event.call_id, event);
  }
  return Object.entries(USAGE_LABELS).map(([kind, label]) => {
    const rows = [...calls.values()].filter((item) => item.kind === kind);
    const reported = rows.filter((item) => typeof item.input_tokens === "number" && typeof item.output_tokens === "number");
    return {
      kind, label, calls: rows.length, reported: reported.length,
      failed: rows.filter((item) => item.status === "failed").length,
      input: reported.reduce((sum, item) => sum + Number(item.input_tokens), 0),
      output: reported.reduce((sum, item) => sum + Number(item.output_tokens), 0),
    };
  }).filter((item) => item.calls > 0);
}
