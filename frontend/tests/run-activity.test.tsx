import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AgentExecution } from "../src/components/AgentExecution";
import { StoppedRunNotice } from "../src/components/StoppedRunNotice";
import { buildAgentRun } from "../src/lib/agentRun";
import { confirmedTaskPlan, currentRunStage, stoppedRunSummary } from "../src/lib/runActivity";
import { INITIAL_CHAT_STATE, reducer, type ChatMessage } from "../src/hooks/useChatStream";
import type { AssistantToolCall, DisplayEvent } from "../src/types/api";

function message(tools: AssistantToolCall[] = [], patch: Partial<ChatMessage> = {}): ChatMessage {
  return { id: "a", role: "assistant", status: "streaming", content: "", markdown: false, phases: [], events: [],
    assistantSteps: [{ id: "s", ordinal: 0, status: "running", content: "", is_final: false, tool_calls: tools }], ...patch };
}
function tool(name: string, status: AssistantToolCall["status"], patch: Partial<AssistantToolCall> = {}): AssistantToolCall {
  return { call_id: name, name, batch_index: 0, status, ...patch };
}
const todos = (statuses = ["completed", "completed", "in_progress", "pending"]) => JSON.stringify({
  todos: statuses.map((status, index) => ({ content: `任务 ${index + 1}`, status })),
});

describe("observed execution stage", () => {
  it("uses active calls and reports concurrent work without inventing overall progress", () => {
    const run = buildAgentRun(message([tool("internet_search", "running"), tool("write_file", "running", { call_id: "w", batch_index: 1 })]));
    expect(currentRunStage(run, [])).toBe("正在生成文件 · 2 项工具调用进行中");
    expect(confirmedTaskPlan(run.steps)).toBeNull();
    const completed = buildAgentRun(message([tool("internet_search", "completed")], { phases: ["responding"] }));
    expect(currentRunStage(completed, [])).toBe("正在生成回复");
  });
  it("distinguishes question/approval and never infers a running stage for terminal history", () => {
    const run = buildAgentRun(message([], { status: "interrupted" }));
    expect(currentRunStage(run, [], "question")).toBe("等待你回答");
    expect(currentRunStage(run, [], "approval")).toBe("等待你确认");
    expect(currentRunStage(buildAgentRun(message([], { status: "pending" })), [])).toBeNull();
    expect(currentRunStage(buildAgentRun(message([], { status: "cancelled" })), [])).toBeNull();
  });
  it("follows subagent tool results and terminal state by call key", () => {
    const run = buildAgentRun(message());
    const events: DisplayEvent[] = [
      { type: "subagent_started", subagent_id: "child" },
      { type: "subagent_tool_call", subagent_id: "child", call_key: "c", name: "internet_search", status: "started" },
      { type: "subagent_tool_call", subagent_id: "child", call_key: "c", name: "internet_search", status: "args", args: '{"query":"test"}' },
    ];
    expect(currentRunStage(run, events)).toBe("正在检索资料");
    events.push({ type: "subagent_tool_result", subagent_id: "child", call_key: "c", name: "internet_search", status: "completed", content: "资料" });
    expect(currentRunStage(run, events)).toBe("子 Agent 正在执行");
    events.push({ type: "subagent_completed", subagent_id: "child" });
    expect(currentRunStage(run, events)).toBe("正在准备");
  });
  it("keeps the latest public phase when stages repeat", () => {
    let state = { ...INITIAL_CHAT_STATE, messages: [message()] };
    for (const phase of ["thinking", "responding", "thinking"] as const) state = reducer(state, { type: "runPhase", phase });
    expect(currentRunStage(buildAgentRun(state.messages[0]), [])).toBe("正在分析");
    expect(state.messages[0].phases).toEqual(["responding", "thinking"]);
  });
});

describe("explicit task plan", () => {
  it("uses only confirmed plans and renders a real 2/4 count and task list", () => {
    const run = buildAgentRun(message([tool("write_todos", "completed", { args_preview: todos() })]));
    expect(confirmedTaskPlan(run.steps)).toMatchObject({ completed: 2, total: 4 });
    render(<AgentExecution run={run} />);
    expect(screen.getByText("任务清单已完成 2/4 项")).toBeTruthy();
    expect(screen.getByText("任务 4")).toBeTruthy();
  });
  it("does not trust pending/failed updates and discards an invalid latest confirmed plan", () => {
    const base = tool("write_todos", "completed", { args_preview: todos() });
    const failed = tool("write_todos", "failed", { call_id: "failed", batch_index: 1, args_preview: todos(["completed"]) });
    const pending = { ...failed, call_id: "pending", status: "running" as const };
    expect(confirmedTaskPlan(buildAgentRun(message([base, failed, pending])).steps)?.total).toBe(4);
    const truncated = { ...failed, call_id: "truncated", status: "completed" as const, args_preview: '{"todos":[' };
    expect(confirmedTaskPlan(buildAgentRun(message([base, truncated])).steps)).toBeNull();
    expect(confirmedTaskPlan(buildAgentRun(message([tool("write_todos", "running", { args_preview: todos() })])).steps)).toBeNull();
  });
  it("accepts explicit revisions but rejects empty or malformed task statuses", () => {
    const first = tool("write_todos", "completed", { args_preview: todos() });
    const revised = { ...first, call_id: "revision", batch_index: 1, args_preview: todos(["completed", "pending"]) };
    expect(confirmedTaskPlan(buildAgentRun(message([first, revised])).steps)).toMatchObject({ completed: 1, total: 2 });
    for (const args_preview of [todos([]), todos(["unknown"]), '{"todos":[{"content":"任务","status":["completed"]}]}', '{"todos":[{"status":"completed"}]}']) {
      expect(confirmedTaskPlan(buildAgentRun(message([{ ...first, args_preview }])).steps)).toBeNull();
    }
  });
});

describe("stop outcome", () => {
  it("preserves received content and separates completed, failed and unconfirmed operations", () => {
    const run = buildAgentRun(message([
      tool("write_file", "completed", { args_preview: '{"file_path":"report.md"}', result_preview: "written" }),
      tool("execute", "running", { args_preview: '{"command":"run-job"}' }),
      tool("delete", "failed"),
    ], { status: "cancelled", content: "部分回答" }));
    expect(run.steps.filter((step) => step.type === "tool_call").map((step) => step.status)).toEqual(["completed", "unknown", "failed"]);
    expect(stoppedRunSummary(run, [])).toMatchObject({ preserved: ["已收到的回答", "工具结果"], completed: ["写入文件 · report.md"], unconfirmed: ["执行命令 · run-job"], failed: ["删除文件"] });
    const onSync = vi.fn(); const { container } = render(<StoppedRunNotice run={run} events={[]} onSync={onSync} />);
    expect(screen.getByText(/停止生成不会撤销已执行的操作/)).toBeTruthy();
    expect(container.querySelector(".mc-icon-spin")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新同步会话，核对结果" }));
    expect(onSync).toHaveBeenCalledOnce();
  });
  it("counts a nested tool once and keeps missing subagent completion explicit", () => {
    const events = [
      { type: "subagent_started", subagent_id: "child" },
      { type: "subagent_text", subagent_id: "child", text: "部分过程" },
      { type: "subagent_tool_call", subagent_id: "child", call_key: "child:c", name: "read_file", status: "started" },
      { type: "subagent_tool_result", subagent_id: "child", call_key: "child:c", name: "read_file", status: "completed", content: "file" },
    ];
    expect(stoppedRunSummary(buildAgentRun(message([], { status: "cancelled" })), events)).toMatchObject({ completed: ["读取文件"], runningAgents: 1, preserved: ["过程文本", "工具结果"] });
  });
});


describe("disconnected execution display", () => {
  it("stops root and subagent spinners without changing observed events or settled results", () => {
    const events: DisplayEvent[] = [
      { type: "subagent_started", subagent_id: "child", name: "分析助手" },
      { type: "subagent_tool_call", subagent_id: "child", call_key: "child:c", name: "read_file", status: "started" },
    ];
    const source = message([
      tool("write_file", "completed", { result_preview: "written" }),
      tool("execute", "running", { batch_index: 1 }),
      tool("edit_file", "waiting", { batch_index: 2 }),
      tool("delete", "failed", { batch_index: 3 }),
    ], { status: "pending", errorCode: "network_disconnected", events });
    const snapshot = JSON.stringify(source);
    const run = buildAgentRun(source);
    expect(run.status).toBe("unconfirmed");
    expect(run.steps.filter((step) => step.type === "tool_call").map((step) => step.status)).toEqual(["completed", "unknown", "unknown", "failed"]);
    const { container } = render(<AgentExecution run={run} events={events} messageStatus="pending" />);
    expect(screen.getByText("执行状态待确认")).toBeTruthy();
    expect(container.querySelector(".mc-icon-spin")).toBeNull();
    expect(JSON.stringify(source)).toBe(snapshot);
  });
});
