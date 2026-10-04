import { useState } from "react";

import { Icon, type IconName } from "./Icon";
import { ExecutionNode } from "./ExecutionNode";
import { Markdown } from "./Markdown";
import { ToolTimeline } from "./ToolTimeline";
import { confirmedTaskPlan, currentRunStage } from "../lib/runActivity";
import { useElapsedMs } from "../hooks/useRunClock";
import {
  formatDuration,
  groupAgentSteps,
  isRunDefaultOpen,
  isRunForcedOpen,
  isRunTimerActive,
  isTerminalRun,
  type AgentRun,
  type AgentRunStatus,
  type AssistantProgressStep,
  type ToolCallStep,
} from "../lib/agentRun";
import { toolCallSummary, toolIconName, toolSummary } from "../lib/toolDisplay";
import type { AssistantToolStatus, DisplayEvent, MessageStatus } from "../types/api";

/**
 * Agent 执行区域：一次运行的过程文本与工具调用按因果顺序排成一段轻时间线。
 *
 * 视觉上刻意不套卡片：过程文本就是普通正文，工具条目是一行灰色小字；
 * 执行状态和具体工具明细分开呈现，具体工具行自带可键盘操作的展开入口。
 *
 * 展开状态是纯前端临时 UI 状态：不写历史、不写 localStorage、不写全局 store。
 */

const RUN_STATUS_LABELS: Record<AgentRunStatus, string> = {
  running: "正在执行",
  waiting: "等待你的操作",
  unconfirmed: "执行状态待确认",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};

const TOOL_STATUS_LABELS: Record<AssistantToolStatus, string> = {
  queued: "待执行",
  running: "执行中",
  completed: "完成",
  failed: "失败",
  waiting: "等待确认",
  unknown: "未收到结果",
};

function runStatusIcon(status: AgentRunStatus): IconName {
  if (status === "completed") return "circle-check";
  if (status === "failed" || status === "cancelled" || status === "unconfirmed") {
    return "circle-alert";
  }
  if (status === "waiting") return "shield-check";
  return "loader-circle";
}

function toolStatusIcon(status: AssistantToolStatus): IconName {
  if (status === "completed") return "circle-check";
  if (status === "failed" || status === "unknown") return "circle-alert";
  if (status === "waiting") return "shield-check";
  return "loader-circle";
}

export function ExecutionHeader({
  run,
  open,
  disabled,
  onToggle,
  stage,
  plan,
}: {
  stage: string | null;
  plan: ReturnType<typeof confirmedTaskPlan>;
  run: AgentRun;
  open: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  // 计时只影响这一个头部组件：步骤列表和 Markdown 不会跟着 tick 重渲染。
  const live = useElapsedMs(run.startedAt, isRunTimerActive(run.status));
  const duration = formatDuration(live ?? run.durationMs);
  const detail = (() => {
    if (run.status === "running") return duration;
    // 等待用户时不显示"耗时未知"：这一轮还没结束，后端也还没给出耗时。
    if (run.status === "waiting" || run.status === "unconfirmed") {
      return duration ? `耗时 ${duration}` : null;
    }
    return duration ? `耗时 ${duration}` : "耗时未知";
  })();
  return (
    <button
      type="button"
      className="agent-execution-head"
      onClick={onToggle}
      disabled={disabled}
      aria-expanded={disabled && run.status !== "running" ? undefined : open}
      aria-label={`${disabled && run.status !== "running" ? "执行状态" : "查看执行步骤"}，${stage ?? RUN_STATUS_LABELS[run.status]}`}
      title={run.status === "running" ? "正在执行，结束后可展开查看步骤" : undefined}
    >
      <Icon
        name={runStatusIcon(run.status)}
        size={16}
        className={run.status === "running" ? "mc-icon-spin" : undefined}
      />
      <span className="agent-execution-state" role="status">{stage ?? RUN_STATUS_LABELS[run.status]}</span>
      {plan ? <span className="agent-plan-progress">任务清单已完成 {plan.completed}/{plan.total} 项</span> : null}
      {detail ? <span className="agent-execution-duration">· {detail}</span> : null}
      {run.status === "unconfirmed" ? (
        <span className="agent-execution-hint">未连接这次执行，请重新同步会话</span>
      ) : null}
      {!disabled || run.status === "running" ? <Icon
        name="chevron-right"
        size={14}
        className="agent-execution-chevron"
        rotate={open ? 90 : 0}
      /> : null}
    </button>
  );
}

/** 当前活动计时放在聊天正文下方，更新只影响这一行。 */
export function ExecutionActivityTiming({ run }: { run: AgentRun }) {
  const activity = run.timings ? [...run.timings.activities].reverse().find((item) => item.status === "started") : undefined;
  const activeTool = run.steps.find((step): step is ToolCallStep => step.type === "tool_call" && step.status === "running" && step.startedAt !== undefined);
  const activityTime = useElapsedMs(activeTool?.startedAt ?? activity?.receivedAt ?? null, run.status === "running" && (!!activity || !!activeTool));
  if (run.status !== "running" || (!activity && !activeTool)) return null;
  return <div className="agent-activity-timing" role="status">
    本次{activeTool ? "工具执行" : activity?.kind === "selection" ? "工具选择" : "模型请求"} {formatDuration(activityTime)}
  </div>;
}

function AssistantProgressItem({ step }: { step: AssistantProgressStep }) {
  const body = <Markdown source={step.content} streaming={step.streaming} />;
  return <div className="agent-step agent-step-progress">{body}</div>;
}

/** 只展示实际可读思考，位于执行摘要下方，不随执行时间线收起。 */
export function ReasoningPanel({ run }: { run: AgentRun }) {
  if (run.reasoning.length === 0) return null;
  const body = run.reasoning.map((step) => <div key={step.id} className="assistant-reasoning-part">
        <Markdown source={step.content} streaming={step.streaming} />
      </div>);
  if (run.status === "running") {
    return <section className="assistant-reasoning" aria-label="思考过程">
      <div className="assistant-reasoning-title">思考过程{run.reasoning.at(-1)?.streaming ? " · 正在生成" : ""}</div>
      <div className="assistant-reasoning-content">{body}</div>
    </section>;
  }
  return <details className="assistant-reasoning">
    <summary>思考过程</summary>
    <div className="assistant-reasoning-content">{body}</div>
  </details>;
}

function ToolCallItem({ tool }: { tool: ToolCallStep }) {
  // 展开状态只跟随当前状态：状态变化后回到默认值，不做持久化。
  const [override, setOverride] = useState<{ status: AssistantToolStatus; open: boolean } | null>(null);
  const open = override?.status === tool.status ? override.open : tool.status === "failed";
  const duration = formatDuration(tool.durationMs);
  const summary = toolCallSummary(tool.argsPreview);
  const hasDetails = Boolean(tool.argsPreview || tool.resultPreview || tool.error);

  return (
    <ExecutionNode
      className={`agent-tool execution-node is-${tool.status}`}
      open={open}
      detailsClassName="agent-tool-details"
      onToggle={(event) =>
        setOverride({ status: tool.status, open: event.currentTarget.open })
      }
      icon={toolIconName(tool.toolName)}
      title={toolSummary(tool.toolName)}
      subtitle={summary}
      status={
        <>
          <Icon
            name={toolStatusIcon(tool.status)}
            size={14}
            className={tool.status === "running" ? "mc-icon-spin" : undefined}
          />
          <span>{TOOL_STATUS_LABELS[tool.status]}{duration ? ` · ${duration}` : ""}</span>
        </>
      }
    >
        {tool.argsPreview ? (
          <>
            <div className="agent-tool-detail-label">参数</div>
            <pre>{tool.argsPreview}</pre>
          </>
        ) : null}
        {tool.resultPreview ? (
          <>
            <div className="agent-tool-detail-label">结果</div>
            <pre>{tool.resultPreview}</pre>
          </>
        ) : null}
        {tool.error ? (
          <>
            <div className="agent-tool-detail-label">错误</div>
            <pre>{tool.error}</pre>
          </>
        ) : null}
        {hasDetails ? null : (
          <div className="agent-tool-detail-label">暂无更多细节</div>
        )}
    </ExecutionNode>
  );
}

export function ExecutionTimeline({
  run,
  events,
  messageStatus,
}: {
  run: AgentRun;
  events: DisplayEvent[];
  messageStatus: MessageStatus | "streaming" | null;
}) {
  if (run.steps.length === 0 && events.length === 0) {
    return null;
  }
  return (
    <div className="agent-timeline">
      {groupAgentSteps(run.steps).map((group) =>
        group.kind === "text" ? (
          <AssistantProgressItem key={group.id} step={group.entry} />
        ) : (
          <div key={group.id} className="agent-tool-list">
            {group.tools.map((tool) => (
              <ToolCallItem key={tool.id} tool={tool} />
            ))}
          </div>
        ),
      )}
      {events.length > 0 ? (
        <ToolTimeline events={events} messageStatus={messageStatus} />
      ) : null}
    </div>
  );
}

export function AgentExecution({
  run,
  events = [],
  messageStatus = null,
  waitingFor,
}: {
  waitingFor?: "question" | "approval";
  run: AgentRun;
  events?: DisplayEvent[];
  messageStatus?: MessageStatus | "streaming" | null;
}) {
  const stage = currentRunStage(run, events, waitingFor);
  const plan = confirmedTaskPlan(run.steps);
  // null = 用户还没手动操作过，按状态默认值；有值后不再被 rerender 或重复完成事件覆盖。
  const [override, setOverride] = useState<boolean | null>(null);
  const forcedOpen = isRunForcedOpen(run.status);
  const hasTimeline = run.steps.length > 0 || events.length > 0;
  const hasTools = run.steps.some((step) => step.type === "tool_call") || events.length > 0;
  // 工具列表完成后仍默认可见；用户可以收起，思考栏始终独立。
  const open = forcedOpen ? true : override ?? (hasTools || isRunDefaultOpen(run.status));
  // 纯文字回复保留状态与真实总耗时；没有观测信息时不造空区域。
  const hasContent =
    hasTimeline || run.reasoning.length > 0 || run.durationMs !== null
    || run.timings !== undefined || !isTerminalRun(run.status);
  if (!hasContent) return null;
  return (
    <section
      className={`agent-execution is-${run.status}${open ? " is-open" : ""}`}
      aria-label="Agent 执行过程"
    >
      <ExecutionHeader
        run={run}
        stage={stage}
        plan={plan}
        open={open}
        disabled={forcedOpen || !hasTimeline}
        onToggle={() => setOverride(!open)}
      />
      <ReasoningPanel run={run} />
      {open && hasTimeline ? (
        <div className="agent-execution-body">
          {plan ? <details className="agent-task-plan">
            <summary>查看任务清单</summary>
            <ul>{plan.items.map((item, index) => <li key={index}>
              <span className={`task-status is-${item.status}`}>{({ pending: "待处理", in_progress: "进行中", completed: "已完成" })[item.status]}</span>
              <span>{item.content}</span>
            </li>)}</ul>
          </details> : null}
          <ExecutionTimeline
            run={run}
            events={events}
            messageStatus={messageStatus}
          />
        </div>
      ) : null}
    </section>
  );
}
