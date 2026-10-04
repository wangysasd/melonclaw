import type { AssistantStep, AssistantToolCall } from "../types/api";
import type { ChatMessage } from "../hooks/useChatStream";
import { toolSummary } from "./toolDisplay";

/**
 * Agent 执行过程的展示模型（从现有消息结构派生，不新增持久化）。
 *
 * 后端协议里没有独立的 run 事件：一次「助手消息」就是一次运行，
 * 过程文本走 `assistant_step_started` / `assistant_text_delta`，
 * 最终回答由 `completed` 事件的 content 明确给出。
 * 这里只做归类，不猜测、不合成后端没有给的信息。
 */

export type AgentRunStatus =
  /** 正在执行：强制展开并走运行级计时。 */
  | "running"
  /** 等待用户审批或回答（HITL）；不是运行结束。 */
  | "waiting"
  /** 历史记录里仍是 pending，但当前没有订阅它的输出：状态待确认。 */
  | "unconfirmed"
  | "completed"
  | "failed"
  | "cancelled";

export interface AssistantProgressStep {
  id: string;
  type: "assistant_progress";
  /** 所属的根 Agent step，用于保持同一步工具的因果顺序。 */
  stepId: string;
  content: string;
  /** 仍在流式生成，用于 Markdown 的流式渲染。 */
  streaming: boolean;
  contentKind?: "text" | "reasoning";
}

export interface ToolCallStep {
  id: string;
  type: "tool_call";
  stepId: string;
  toolCallId: string;
  toolName: string;
  argsPreview: string | null;
  resultPreview: string | null;
  error: string | null;
  status: AssistantToolCall["status"];
  /** 服务端同时给了开始与结束时间才计算，否则为 null（不编造耗时）。 */
  durationMs: number | null;
  startedAt?: number;
}

export type AgentStep = AssistantProgressStep | ToolCallStep;

/**
 * 时间线的一段：过程文本单独成段，同一步的工具保持为一组以便维持因果顺序。
 *
 * 这只是数据层的排序分组，不对应额外的可交互摘要层；展示层会直接逐条渲染实际工具调用。
 */
export interface AgentTextGroup {
  kind: "text";
  id: string;
  entry: AssistantProgressStep;
}

export interface AgentToolGroup {
  kind: "tools";
  id: string;
  stepId: string;
  tools: ToolCallStep[];
}

export type AgentStepGroup = AgentTextGroup | AgentToolGroup;

/** 按 step 聚合同一步的工具；聚合同一步骤的工具调用保持调用顺序。 */
export function groupAgentSteps(steps: AgentStep[]): AgentStepGroup[] {
  const groups: AgentStepGroup[] = [];
  for (const entry of steps) {
    if (entry.type === "assistant_progress") {
      groups.push({ kind: "text", id: entry.id, entry });
      continue;
    }
    const last = groups.at(-1);
    if (last?.kind === "tools" && last.stepId === entry.stepId) {
      last.tools.push(entry);
      continue;
    }
    groups.push({
      kind: "tools",
      id: `${entry.stepId}:tools`,
      stepId: entry.stepId,
      tools: [entry],
    });
  }
  return groups;
}

export interface AgentRun {
  /** 展示用的运行标识：一次运行对应一条助手消息。 */
  id: string;
  conversationId: string | null;
  status: AgentRunStatus;
  /** 当前消息最后观测到的公开阶段，不读取别的消息的全局状态。 */
  timings?: ChatMessage["timings"];
  phase: ChatMessage["phases"][number] | null;
  startedAt: number | null;
  completedAt: number | null;
  /** 终态耗时：优先后端 execution_duration_ms。 */
  durationMs: number | null;
  /** 过程步骤（按 ordinal + batch_index 的因果顺序）。 */
  steps: AgentStep[];
  /** 模型实际返回的可读思考，与过程折叠状态独立。 */
  reasoning: AssistantProgressStep[];
  /** 当前根模型步骤的正文预览；工具调用出现时归回执行过程。 */
  liveAnswer: string | null;
  /** 最终回答正文；与执行过程分离，不受折叠影响。 */
  finalAnswer: string | null;
}

/** 收起状态也要能看出这一轮调用了哪些工具。 */
export interface AgentToolSummary {
  count: number;
  /** 去重后的工具名（展示层友好名），最多 3 个。 */
  names: string[];
  /** 超出 3 个时的剩余数量。 */
  more: number;
  running: number;
  failed: number;
}

/** 工具摘要只统计工具条目本身，不掺入过程文本。 */
export function summariseAgentTools(steps: AgentStep[]): AgentToolSummary | null {
  const tools = steps.filter(
    (step): step is ToolCallStep => step.type === "tool_call",
  );
  if (tools.length === 0) return null;
  const names: string[] = [];
  for (const tool of tools) {
    const label = toolSummary(tool.toolName);
    if (!names.includes(label)) names.push(label);
  }
  return {
    count: tools.length,
    names: names.slice(0, 3),
    more: Math.max(0, names.length - 3),
    running: tools.filter((tool) => tool.status === "running").length,
    failed: tools.filter(
      (tool) => tool.status === "failed" || tool.status === "unknown",
    ).length,
  };
}

const TERMINAL_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

export function isTerminalRun(status: AgentRunStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

/** running 的运行必须保持展开；其余状态允许用户自行折叠。 */
export function isRunForcedOpen(status: AgentRunStatus): boolean {
  return status === "running";
}

/** 状态决定的默认值；完成后的工具列表由展示组件保持展开，纯文字过程收起。 */
export function isRunDefaultOpen(status: AgentRunStatus): boolean {
  return status !== "completed";
}

/** 计时只服务 running：其余状态一律显示固定耗时或不显示。 */
export function isRunTimerActive(status: AgentRunStatus): boolean {
  return status === "running";
}

function runStatusFromMessage(message: ChatMessage): AgentRunStatus {
  switch (message.status) {
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    case "interrupted":
      return "waiting";
    case "pending":
      // 历史里还是 pending 但当前没有订阅：只说明"没跑完或没连上"，
      // 不能继续显示正在执行，也不能伪造成成功。
      return "unconfirmed";
    default:
      return "running";
  }
}

function toolDuration(tool: AssistantToolCall): number | null {
  if (typeof tool.duration_ms === "number") return Math.max(0, tool.duration_ms);
  const { started_at: startedAt, completed_at: completedAt } = tool;
  if (typeof startedAt !== "number" || typeof completedAt !== "number") return null;
  const duration = completedAt - startedAt;
  return duration > 0 ? duration : null;
}

function stepToEntries(step: AssistantStep): AgentStep[] {
  const entries: AgentStep[] = [];
  const tools = [...step.tool_calls].sort((a, b) => a.batch_index - b.batch_index);
  const blocks = step.content_blocks ?? [{ type: "text" as const, text: step.content }];
  for (const [index, block] of blocks.entries()) {
    if (block.text.trim()) entries.push({
      id: `${step.id}:text:${index}`, type: "assistant_progress", stepId: step.id,
      content: block.text, streaming: false, contentKind: block.type,
    });
  }

  for (const tool of tools) {
    entries.push({
      id: `${step.id}:tool:${tool.call_id}`,
      type: "tool_call",
      stepId: step.id,
      toolCallId: tool.call_id,
      toolName: tool.name,
      argsPreview: tool.args_preview ?? null,
      resultPreview: tool.result_preview ?? null,
      error: tool.error ?? null,
      status: tool.status,
      durationMs: toolDuration(tool),
      startedAt: tool.received_at,
    });
  }
  return entries;
}

/**
 * 把助手消息归约成 AgentRun。
 *
 * - 工具结果按 `call_id` 合并进对应工具步骤，同名工具重复调用也各自独立。
 * - 只有后端终态快照标记的最终 step（`is_final`）才是最终回答；
 *   流式期间最近一个无工具 step 的正文只是临时预览。
 * - 纯函数：不读时间、不写状态，便于测试与复用。
 */
export function buildAgentRun(
  message: ChatMessage,
  conversationId: string | null = null,
): AgentRun {
  const status = runStatusFromMessage(message);
  const ordered = [...message.assistantSteps].sort(
    (left, right) => left.ordinal - right.ordinal,
  );
  const finalStep = ordered.find((step) => step.is_final) ?? null;
  const latest = ordered.at(-1) ?? null;
  const liveStep = status === "running" && latest && latest.tool_calls.length === 0
    ? latest : null;
  const liveAnswer = liveStep
    ? (liveStep.content_blocks ?? [{ type: "text" as const, text: liveStep.content }])
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .join("") || null
    : null;

  const steps: AgentStep[] = [];
  const reasoning: AssistantProgressStep[] = [];
  for (const step of ordered) {
    const entries = stepToEntries(step);
    if (isTerminalRun(status) || status === "unconfirmed") {
      for (const entry of entries) {
        if (entry.type === "tool_call" && (entry.status === "queued" || entry.status === "running" || entry.status === "waiting")) entry.status = "unknown";
      }
    }
    for (const entry of entries) {
      if (entry.type === "assistant_progress" && entry.contentKind === "reasoning") {
        reasoning.push(entry);
      } else if (step.id !== finalStep?.id && (step.id !== liveStep?.id || entry.type !== "assistant_progress")) {
        steps.push(entry);
      }
    }
  }
  // 只把最后一段过程文本标成流式：避免每来一个 delta 就重渲染整段时间线的 Markdown。
  const tail = steps.at(-1);
  if (status === "running" && tail?.type === "assistant_progress" && tail.stepId === latest?.id) {
    tail.streaming = true;
  }
  if (status === "running" && reasoning.at(-1)?.stepId === latest?.id
    && latest?.content_blocks?.at(-1)?.type === "reasoning") {
    reasoning[reasoning.length - 1].streaming = true;
  }

  const startedAt =
    message.startedAt ??
    (message.timestamp ? Date.parse(message.timestamp) || null : null);
  const durationMs =
    typeof message.executionDurationMs === "number"
      ? message.executionDurationMs
      : null;
  const completedAt =
    message.completedAt ??
    (startedAt !== null && durationMs !== null ? startedAt + durationMs : null);

  return {
    id: message.id,
    conversationId,
    status,
    phase: message.phases.at(-1) ?? null,
    timings: message.timings,
    startedAt,
    completedAt,
    durationMs: durationMs ?? (
      completedAt !== null && startedAt !== null && completedAt >= startedAt
        ? completedAt - startedAt
        : null
    ),
    steps,
    reasoning,
    liveAnswer,
    finalAnswer: isTerminalRun(status) && message.content.trim()
      ? message.content
      : null,
  };
}

/** 8.3s / 12.8s / 1m 08s；没有可靠时间信息时返回 null（不显示 0.0s）。 */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  if (!Number.isFinite(ms) || ms < 0) return null;
  // 毫秒级的快速工具保留一位小数只会显示成 0.0s，看起来像没测到时间。
  if (ms > 0 && ms < 100) return "<0.1s";
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const totalSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  return `${minutes}m ${String(totalSeconds % 60).padStart(2, "0")}s`;
}
