import type { DisplayEvent, AssistantToolStatus } from "../types/api";
import type { AgentRun, ToolCallStep } from "./agentRun";
import { toolCallSummary, toolSummary } from "./toolDisplay";

interface ObservedTool {
  id: string;
  name: string;
  status: AssistantToolStatus;
  args: string | null;
  result: string | null;
}

/** 子 Agent 的实际工具调用按服务端 call_key 对账，不计合成的父任务节点。 */
function subagentActivity(events: DisplayEvent[]) {
  const agents = new Map<string, boolean>();
  const tools = new Map<string, ObservedTool & { agentId: string }>();
  for (const event of events) {
    if (typeof event.subagent_id !== "string") continue;
    const agentId = event.subagent_id;
    if (event.type === "subagent_started") agents.set(agentId, true);
    if (event.type === "subagent_completed" || event.type === "subagent_failed") {
      agents.set(agentId, false);
      for (const tool of tools.values()) {
        if (tool.agentId === agentId && tool.status === "running") tool.status = "unknown";
      }
    }
    if (!["subagent_tool_call", "subagent_tool_result"].includes(event.type)
      || typeof event.call_key !== "string" || typeof event.name !== "string") continue;
    const previous = tools.get(event.call_key);
    const status = event.status === "failed" ? "failed" : event.status === "waiting" ? "waiting"
      : event.type === "subagent_tool_result" ? "completed" : "running";
    tools.set(event.call_key, {
      id: event.call_key, agentId, name: event.name, status,
      args: event.type === "subagent_tool_call"
        ? typeof event.args === "string" ? event.args : event.args ? JSON.stringify(event.args) : previous?.args ?? null
        : previous?.args ?? null,
      result: event.type === "subagent_tool_result" && typeof event.content === "string"
        ? event.content : previous?.result ?? null,
    });
  }
  return { tools: [...tools.values()], runningAgents: [...agents.values()].filter(Boolean).length };
}

function observedTools(run: AgentRun, events: DisplayEvent[]): ObservedTool[] {
  return [
    ...run.steps.filter((step): step is ToolCallStep => step.type === "tool_call").map((tool) => ({
      id: tool.id, name: tool.toolName, status: tool.status, args: tool.argsPreview, result: tool.resultPreview,
    })),
    ...subagentActivity(events).tools,
  ];
}

const TOOL_STAGES: Record<string, string> = {
  internet_search: "正在检索资料",
  read_file: "正在读取文件",
  ls: "正在查看目录",
  glob: "正在查找文件",
  grep: "正在查找内容",
  write_file: "正在生成文件",
  edit_file: "正在修改文件",
  delete: "正在删除文件",
  execute: "正在执行命令",
  eval: "正在计算",
  write_todos: "正在更新任务清单",
  task: "子 Agent 正在执行",
  confirm_skill_install: "正在安装 Skill",
  confirm_mcp_install: "正在安装 MCP",
  test_mcp_install: "正在测试 MCP 连接",
};

const PHASE_STAGES: Record<string, string> = {
  starting: "正在准备",
  selecting_tools: "正在选择工具",
  thinking: "正在分析",
  responding: "正在生成回复",
  processing: "正在处理工具结果",
};

export function currentRunStage(run: AgentRun, events: DisplayEvent[], waitingFor?: "question" | "approval") {
  if (run.status === "waiting") {
    return waitingFor === "question" ? "等待你回答" : waitingFor === "approval" ? "等待你确认" : "等待你的操作";
  }
  if (run.status !== "running") return null;
  const tools = observedTools(run, events).filter((tool) => tool.status === "running");
  const latest = tools.at(-1);
  if (latest) {
    const stage = TOOL_STAGES[latest.name] ?? `正在调用工具：${toolSummary(latest.name)}`;
    return tools.length > 1 ? `${stage} · ${tools.length} 项工具调用进行中` : stage;
  }
  if (subagentActivity(events).runningAgents > 0) return "子 Agent 正在执行";
  return PHASE_STAGES[run.phase ?? ""] ?? (run.steps.at(-1)?.type === "assistant_progress" ? "正在生成回复" : "正在准备");
}

export interface TaskPlan {
  items: { content: string; status: "pending" | "in_progress" | "completed" }[];
  completed: number;
  total: number;
}

/** 只有成功返回的明确清单提供分母；最新清单无效时不沿用过期的旧分母。 */
export function confirmedTaskPlan(steps: AgentRun["steps"]): TaskPlan | null {
  const latest = steps.filter((step): step is ToolCallStep =>
    step.type === "tool_call" && step.toolName === "write_todos" && step.status === "completed" && !step.error).at(-1);
  if (!latest?.argsPreview) return null;
  try {
    const args = JSON.parse(latest.argsPreview);
    if (!Array.isArray(args.todos) || args.todos.length === 0 || args.todos.length > 100) return null;
    if (!args.todos.every((item: unknown) => item && typeof item === "object"
      && "content" in item && typeof item.content === "string" && item.content.trim()
      && "status" in item && typeof item.status === "string" && ["pending", "in_progress", "completed"].includes(item.status))) return null;
    const items: TaskPlan["items"] = args.todos;
    return { items, completed: items.filter((item) => item.status === "completed").length, total: items.length };
  } catch {
    return null;
  }
}

/** 取消只结束订阅：没有结果的调用不能被当成失败、成功或已撤销。 */
export function stoppedRunSummary(run: AgentRun, events: DisplayEvent[]) {
  const tools = observedTools(run, events);
  const describe = (tool: ObservedTool) => {
    const args = toolCallSummary(tool.args);
    return `${toolSummary(tool.name)}${args ? ` · ${args}` : ""}`;
  };
  const preserved: string[] = [];
  if (run.finalAnswer) preserved.push("已收到的回答");
  if (run.steps.some((step) => step.type === "assistant_progress" && step.content.trim())
    || events.some((event) => event.type === "subagent_text" && typeof event.text === "string" && event.text.trim())) preserved.push("过程文本");
  if (tools.some((tool) => tool.result)) preserved.push("工具结果");
  return {
    preserved,
    completed: tools.filter((tool) => tool.status === "completed").map(describe),
    failed: tools.filter((tool) => tool.status === "failed").map(describe),
    unconfirmed: tools.filter((tool) => tool.status === "running" || tool.status === "waiting" || tool.status === "unknown").map(describe),
    runningAgents: subagentActivity(events).runningAgents,
  };
}
