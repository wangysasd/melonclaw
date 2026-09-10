import { useMemo, useState } from "react";

import { Icon } from "./Icon";
import { ToolTimeline } from "./ToolTimeline";
import type { ChatMessage, ReasoningPhase } from "../hooks/useChatStream";
import type { DisplayEvent, MessageStatus } from "../types/api";

interface SummaryStep {
  label: string;
  detail?: string;
}

const PHASE_LABELS: Record<ReasoningPhase, string> = {
  starting: "准备执行",
  selecting_tools: "筛选相关工具",
  thinking: "组织回答思路",
  processing: "执行工具或子任务",
  responding: "生成回复",
  waiting: "等待你的确认",
};

function uniqueToolNames(events: DisplayEvent[]): string[] {
  const names = new Set<string>();
  for (const event of events) {
    if (event.type !== "tool_call" && event.type !== "subagent_tool_call") continue;
    if (typeof event.name === "string" && event.name.trim()) names.add(event.name.trim());
  }
  return [...names];
}

/**
 * 生成安全的执行摘要。它只依据阶段和工具事件，不读取或展示模型原始思维链。
 */
export function buildReasoningSteps(
  phases: ReasoningPhase[],
  events: DisplayEvent[],
  status: ChatMessage["status"],
): SummaryStep[] {
  const steps: SummaryStep[] = phases.map((phase) => ({ label: PHASE_LABELS[phase] }));
  const toolNames = uniqueToolNames(events);
  if (toolNames.length > 0) {
    steps.push({
      label: "已调用相关工具",
      detail: toolNames.length > 3
        ? `${toolNames.slice(0, 3).join("、")} 等 ${toolNames.length} 项`
        : toolNames.join("、"),
    });
  }
  const subagentCount = new Set(
    events
      .filter((event) => event.type === "subagent_started")
      .map((event) => String(event.subagent_id ?? ""))
      .filter(Boolean),
  ).size;
  if (subagentCount > 0) {
    steps.push({ label: "已委派子任务", detail: `${subagentCount} 个` });
  }
  if (status === "completed") steps.push({ label: "回复已生成" });
  if (status === "failed") steps.push({ label: "执行未完成" });
  if (status === "interrupted" && !phases.includes("waiting")) {
    steps.push({ label: "等待你的确认" });
  }
  return steps;
}

export function ReasoningSummary({
  phases,
  events,
  status,
}: {
  phases: ReasoningPhase[];
  events: DisplayEvent[];
  status: MessageStatus | "streaming" | null;
}) {
  const [expanded, setExpanded] = useState(status === "streaming");
  const steps = useMemo(() => buildReasoningSteps(phases, events, status), [phases, events, status]);
  if (steps.length === 0 && events.length === 0) return null;

  return (
    <details
      className="reasoning-summary"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="reasoning-summary-head">
        <Icon name="list-checks" size={16} />
        <span className="reasoning-summary-title">思路摘要</span>
        <span className="reasoning-summary-note">执行阶段与工具活动</span>
        <Icon name="chevron-right" size={15} className="reasoning-summary-chevron" />
      </summary>
      <div className="reasoning-summary-body">
        {steps.length > 0 ? (
          <ol className="reasoning-steps">
            {steps.map((step, index) => (
              <li key={`${step.label}-${index}`}>
                <span className="reasoning-step-marker">{index + 1}</span>
                <span>
                  <span className="reasoning-step-label">{step.label}</span>
                  {step.detail ? <span className="reasoning-step-detail">{step.detail}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        ) : null}
        {events.length > 0 ? <ToolTimeline events={events} messageStatus={status} /> : null}
        <p className="reasoning-summary-footnote">这里显示可验证的执行摘要，不包含模型隐藏推理文本。</p>
      </div>
    </details>
  );
}
