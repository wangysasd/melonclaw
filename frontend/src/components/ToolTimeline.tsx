import { memo, useMemo, useState, type ReactNode } from "react";

import { Icon } from "./Icon";
import type { DisplayEvent, MessageStatus } from "../types/api";

/**
 * 工具时间线：将消息的 display events 归约为「工具卡片 + 嵌套子 Agent 卡片」的树，
 * 将工具调用、工具结果和子 Agent 事件组合成可折叠时间线。
 *
 * - 工具卡按 call_key 匹配调用与结果；无 call_key 时用序号回退。
 * - 子 Agent 卡按 subagent_id 归并文本、工具计数与终态；parent_call_id 命中
 *   task 工具时嵌套到其 children，未命中时挂到根时间线。
 * - 流式与历史重放共用同一归约器；React 按消息分组渲染，天然隔离不同消息。
 */

import { toolSummary } from "../lib/toolDisplay";

function formatToolValue(value: unknown, fallback = "{}"): string {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

type ToolStatus = "started" | "completed" | "failed" | "waiting" | "unknown";
type SubagentStatus = "running" | "completed" | "failed" | "waiting" | "unknown";

interface ToolNode {
  kind: "tool";
  key: string;
  name: string;
  args?: string;
  output?: string;
  status: ToolStatus;
  children: TimelineEntry[];
}

interface SubagentNode {
  kind: "subagent";
  id: string;
  name: string;
  text: string;
  toolCount: number;
  status: SubagentStatus;
  tools: ToolNode[];
}

type TimelineEntry = ToolNode | SubagentNode;

function hasFailure(entry: TimelineEntry): boolean {
  return entry.status === "failed" || (entry.kind === "tool" ? entry.children : entry.tools).some(hasFailure);
}

function normalizeToolStatus(status: unknown): ToolStatus {
  if (status === "failed") return "failed";
  if (status === "completed") return "completed";
  return "started";
}

export function buildTimeline(events: DisplayEvent[], messageStatus?: MessageStatus | "streaming" | null): TimelineEntry[] {
  const root: TimelineEntry[] = [];
  const toolMap = new Map<string, ToolNode>();
  const subagentMap = new Map<string, SubagentNode>();
  const anonymousPending = new Map<string, string[]>();
  const eventKey = (event: DisplayEvent): string => {
    if (event.call_key) return String(event.call_key);
    const scope = `${event.subagent_id ?? "root"}:${event.name ?? "tool"}`;
    const pending = anonymousPending.get(scope) ?? [];
    if (event.type.endsWith("_result") && pending.length) return pending.shift()!;
    const key = `anonymous:${toolMap.size}:${scope}`;
    if (event.type.endsWith("_call")) {
      pending.push(key);
      anonymousPending.set(scope, pending);
    }
    return key;
  };

  const appendRoot = (entry: TimelineEntry) => {
    if (!root.includes(entry)) root.push(entry);
  };

  const ensureTool = (
    key: string,
    name: unknown,
    status: unknown,
  ): ToolNode => {
    let tool = toolMap.get(key);
    if (!tool) {
      tool = {
        kind: "tool",
        key,
        name: String(name || "unknown"),
        status: normalizeToolStatus(status),
        children: [],
      };
      toolMap.set(key, tool);
    }
    return tool;
  };

  const ensureSubagent = (event: DisplayEvent): SubagentNode | null => {
    const id = String(event.subagent_id ?? "");
    if (!id) return null;
    const existing = subagentMap.get(id);
    if (existing) return existing;
    const node: SubagentNode = {
      kind: "subagent",
      id,
      name: String(event.subagent_name || "general-purpose"),
      text: "",
      toolCount: 0,
      status: "running",
      tools: [],
    };
    subagentMap.set(id, node);
    // 对齐旧 parent 匹配：优先带 subagent 前缀的组合 key，再退回 id: 前缀。
    const parentCallId = event.parent_call_id ? String(event.parent_call_id) : null;
    let parent: ToolNode | null = null;
    if (parentCallId) {
      const keys: string[] = [];
      if (
        parentCallId.startsWith("history:") ||
        parentCallId.startsWith("id:")
      ) {
        keys.push(parentCallId);
      } else {
        keys.push(`id:${parentCallId}`);
      }
      if (event.parent_subagent_id && parentCallId.includes(":id:")) {
        keys.unshift(
          `${event.parent_subagent_id}:id:${parentCallId.split(":id:").pop()}`,
        );
      }
      for (const candidate of keys) {
        const found = toolMap.get(candidate);
        if (found) {
          parent = found;
          break;
        }
      }
      if (!parent) {
        parent = ensureTool(keys[0], "task", "started");
        appendRoot(parent);
      }
    }
    (parent ? parent.children : root).push(node);
    return node;
  };

  for (const event of events) {
    switch (event.type) {
      case "tool_call": {
        const key = eventKey(event);
        const tool = ensureTool(key, event.name, event.status);
        appendRoot(tool);
        if (event.name) tool.name = String(event.name);
        if (event.args !== undefined) {
          tool.args = formatToolValue(event.args);
        }
        tool.status = normalizeToolStatus(event.status);
        break;
      }
      case "tool_result": {
        const key = eventKey(event);
        const tool = ensureTool(key, event.name, "started");
        appendRoot(tool);
        tool.status = event.status === "failed" ? "failed" : "completed";
        tool.output = formatToolValue(event.content, "<无文本输出>");
        break;
      }
      case "subagent_started": {
        ensureSubagent(event);
        break;
      }
      case "subagent_text": {
        const node = ensureSubagent(event);
        if (node) node.text += String(event.text || "");
        break;
      }
      case "subagent_tool_call": {
        const node = ensureSubagent(event);
        if (!node) break;
        const key = eventKey(event);
        const tool = ensureTool(key, event.name, event.status);
        if (!node.tools.includes(tool)) node.tools.push(tool);
        if (event.name) tool.name = String(event.name);
        if (event.args !== undefined) {
          tool.args = formatToolValue(event.args);
        }
        tool.status = normalizeToolStatus(event.status);
        node.toolCount = node.tools.length;
        break;
      }
      case "subagent_tool_result": {
        const node = ensureSubagent(event);
        if (!node) break;
        const key = eventKey(event);
        const tool = ensureTool(key, event.name, "started");
        if (!node.tools.includes(tool)) node.tools.push(tool);
        tool.status = event.status === "failed" ? "failed" : "completed";
        tool.output = formatToolValue(event.content, "<无文本输出>");
        node.toolCount = node.tools.length;
        break;
      }
      case "subagent_completed": {
        const node = ensureSubagent(event);
        if (node) node.status = "completed";
        break;
      }
      case "subagent_failed": {
        const node = ensureSubagent(event);
        if (node) {
          node.status = "failed";
          if (event.error && !node.text) node.text = String(event.error);
        }
        break;
      }
      default:
        break;
    }
  }
  const inactive = messageStatus === "interrupted" ? "waiting" :
    messageStatus === "failed" || messageStatus === "cancelled" || messageStatus === "completed" ? "unknown" : null;
  if (inactive) {
    for (const tool of toolMap.values()) if (tool.status === "started") tool.status = inactive;
    for (const node of subagentMap.values()) if (node.status === "running") node.status = inactive;
  }
  return root;
}

const TOOL_STATUS_LABELS: Record<ToolStatus, string> = {
  started: "处理中",
  completed: "已完成",
  failed: "失败",
  waiting: "已暂停，等待确认",
  unknown: "未收到执行结果",
};

const SUBAGENT_STATUS_LABELS: Record<SubagentStatus, string> = {
  running: "处理中",
  completed: "已完成",
  failed: "失败",
  waiting: "已暂停，等待确认",
  unknown: "未收到执行结果",
};

function ToolCard({ tool }: { tool: ToolNode }) {
  const [expanded, setExpanded] = useState<{ status: ToolStatus; open: boolean } | null>(null);
  const open = expanded?.status === tool.status ? expanded.open : hasFailure(tool);

  const headIcon =
    tool.name === "task" ? "list-checks" : "file-text";
  const statusIcon =
    tool.status === "completed"
      ? "circle-check"
      : tool.status === "failed"
        ? "circle-alert"
        : tool.status === "waiting"
          ? "shield-check"
          : tool.status === "unknown"
            ? "circle-alert"
            : "loader-circle";

  return (
    <details
      className={[
        "tool-card",
        tool.status === "completed" ? "is-complete" : "",
        tool.status === "failed" ? "is-failed" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      open={open}
      onToggle={(event) => setExpanded({ status: tool.status, open: event.currentTarget.open })}
    >
      <summary className="tool-head">
        <Icon
          name={headIcon}
          size={17}
        />
        <span className="tool-summary">{toolSummary(tool.name)}</span>
        <span className="tool-name">{tool.name}</span>
        <span className="tool-status">
          <Icon
            name={statusIcon}
            size={15}
            className={tool.status === "started" ? "mc-icon-spin" : undefined}
          />
          <span className="tool-status-label">
            {TOOL_STATUS_LABELS[tool.status]}
          </span>
        </span>
      </summary>
      <div className="tool-details">
        {tool.args !== undefined && <><div className="tool-detail-label">输入参数</div><pre className="tool-args">{tool.args}</pre></>}
        {tool.output !== undefined && (
          <><div className="tool-detail-label">{tool.status === "failed" ? "错误详情" : "执行结果"}</div><pre className="tool-output">{tool.output}</pre></>
        )}
        {tool.children.length > 0 && (
          <div className="tool-children">
            {tool.children.map((child) => (
              <TimelineEntryView key={child.kind === "tool" ? child.key : child.id} entry={child} />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

function SubagentCard({ node }: { node: SubagentNode }) {
  const [expanded, setExpanded] = useState<{ status: SubagentStatus; open: boolean } | null>(null);
  const open = expanded?.status === node.status ? expanded.open : hasFailure(node);

  const failed = node.status === "failed";

  return (
    <details
      className={["subagent-card", failed ? "is-failed" : ""]
        .filter(Boolean)
        .join(" ")}
      open={open}
      onToggle={(event) => setExpanded({ status: node.status, open: event.currentTarget.open })}
    >
      <summary className="subagent-head">
        <Icon name="list-checks" size={16} className="subagent-icon" />
        <span className="subagent-name">{node.name}</span>
        <span className="subagent-status">
          {SUBAGENT_STATUS_LABELS[node.status]} · {node.toolCount} 个工具
        </span>
      </summary>
      <div className="subagent-details">
        {node.text ? <div className="subagent-output">{node.text}</div> : null}
        {node.tools.length > 0 && (
          <div className="subagent-tools">
            {node.tools.map((tool) => (
              <ToolCard key={tool.key} tool={tool} />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}

function TimelineEntryView({ entry }: { entry: TimelineEntry }): ReactNode {
  return entry.kind === "tool" ? (
    <ToolCard tool={entry} />
  ) : (
    <SubagentCard node={entry} />
  );
}

export const ToolTimeline = memo(function ToolTimeline({ events, messageStatus }: { events: DisplayEvent[]; messageStatus?: MessageStatus | "streaming" | null }) {
  const timeline = useMemo(() => buildTimeline(events, messageStatus), [events, messageStatus]);
  if (timeline.length === 0) return null;
  return (
    <div className="message-tools" aria-label="工具活动">
      <div className="tool-detail-label">工具活动 · {timeline.length} 项</div>
      {timeline.map((entry) => (
        <TimelineEntryView
          key={entry.kind === "tool" ? entry.key : entry.id}
          entry={entry}
        />
      ))}
    </div>
  );
});
