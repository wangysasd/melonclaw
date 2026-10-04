import { summarizeModelUsage } from "../lib/modelUsage";
import type { DisplayEvent } from "../types/api";

export function MessageUsage({ events }: { events: DisplayEvent[] }) {
  const usage = summarizeModelUsage(events).reduce((total, row) => ({
    calls: total.calls + row.calls,
    reported: total.reported + row.reported,
    input: total.input + row.input,
    output: total.output + row.output,
  }), { calls: 0, reported: 0, input: 0, output: 0 });
  if (usage.reported === 0) return null;
  return <div className="message-token-usage" aria-label="输入输出 token 数" title={usage.reported < usage.calls ? "仅汇总已报告的 token 用量" : undefined}>
    输入 {usage.input} | 输出 {usage.output}
  </div>;
}
