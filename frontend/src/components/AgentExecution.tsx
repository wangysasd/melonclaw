import { useState } from "react";

import { Icon, type IconName } from "./Icon";
import { ExecutionNode } from "./ExecutionNode";
import { Markdown } from "./Markdown";
import { ToolTimeline } from "./ToolTimeline";
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
}: {
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
      aria-expanded={open}
      aria-label={`查看执行步骤，${RUN_STATUS_LABELS[run.status]}`}
      title={disabled ? "正在执行，结束后可展开查看步骤" : undefined}
    >
      <Icon
        name={runStatusIcon(run.status)}
        size={15}
        className={run.status === "running" ? "mc-icon-spin" : undefined}
      />
      <span className="agent-execution-state">{RUN_STATUS_LABELS[run.status]}</span>
      {detail ? <span className="agent-execution-duration">· {detail}</span> : null}
      {run.status === "unconfirmed" ? (
        <span className="agent-execution-hint">未连接这次执行，请重新同步会话</span>
      ) : null}
      <Icon
        name="chevron-right"
        size={14}
        className="agent-execution-chevron"
        rotate={open ? 90 : 0}
      />
    </button>
  );
}

function AssistantProgressItem({ step }: { step: AssistantProgressStep }) {
  return (
    <div className="agent-step agent-step-progress">
      <Markdown source={step.content} streaming={step.streaming} />
    </div>
  );
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
  phaseLabel,
}: {
  run: AgentRun;
  events: DisplayEvent[];
  messageStatus: MessageStatus | "streaming" | null;
  phaseLabel?: string;
}) {
  if (run.steps.length === 0 && events.length === 0) {
    // 只有确实还在跑的时候才显示阶段占位；等待/待确认时给转圈会误导。
    if (!phaseLabel || run.status !== "running") return null;
    return (
      <div className="agent-step-pending" role="status">
        <Icon name="loader-circle" size={15} className="mc-icon-spin" />
        {`${phaseLabel}…`}
      </div>
    );
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
  phaseLabel,
}: {
  run: AgentRun;
  events?: DisplayEvent[];
  messageStatus?: MessageStatus | "streaming" | null;
  phaseLabel?: string;
}) {
  // null = 用户还没手动操作过，按状态默认值；有值后不再被 rerender 或重复完成事件覆盖。
  const [override, setOverride] = useState<boolean | null>(null);
  const forcedOpen = isRunForcedOpen(run.status);
  // 运行中强制展开；完成后默认收起，用户点击状态摘要即可恢复完整时间线。
  const open = forcedOpen ? true : override ?? isRunDefaultOpen(run.status);
  // 旧历史消息可能既没有步骤也没有事件：保持原样展示，不凭空造一个空执行区域。
  const hasContent =
    run.steps.length > 0 || events.length > 0 || !isTerminalRun(run.status);
  if (!hasContent) return null;
  return (
    <section
      className={`agent-execution is-${run.status}${open ? " is-open" : ""}`}
      aria-label="Agent 执行过程"
    >
      <ExecutionHeader
        run={run}
        open={open}
        disabled={forcedOpen}
        onToggle={() => setOverride(!open)}
      />
      {open ? (
        <div className="agent-execution-body">
          <ExecutionTimeline
            run={run}
            events={events}
            messageStatus={messageStatus}
            phaseLabel={phaseLabel}
          />
        </div>
      ) : null}
    </section>
  );
}
