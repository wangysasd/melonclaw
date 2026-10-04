import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentExecution } from "../src/components/AgentExecution";
import { INITIAL_CHAT_STATE, reducer, type ChatMessage } from "../src/hooks/useChatStream";
import {
  buildAgentRun,
  formatDuration,
  groupAgentSteps,
  summariseAgentTools,
  type ToolCallStep,
} from "../src/lib/agentRun";
import { toolCallSummary } from "../src/lib/toolDisplay";
import type { AssistantStep } from "../src/types/api";

function assistant(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "",
    status: "streaming",
    markdown: false,
    events: [],
    assistantSteps: [],
    phases: [],
    startedAt: 1_000,
    ...overrides,
  };
}

function progressStep(overrides: Partial<AssistantStep> = {}): AssistantStep {
  return {
    id: "run:step:0",
    ordinal: 0,
    content: "",
    status: "streaming",
    is_final: false,
    tool_calls: [],
    ...overrides,
  };
}

describe("agent run derivation", () => {
  it("preserves interleaved reasoning and text, including reasoning in a final step", () => {
    const blocks = [
      { type: "reasoning" as const, text: "分析一" },
      { type: "text" as const, text: "行动说明" },
      { type: "reasoning" as const, text: "分析二" },
    ];
    const run = buildAgentRun(assistant({ assistantSteps: [progressStep({ content: "分析一行动说明分析二", content_blocks: blocks })] }));
    expect(run.steps.filter((item) => item.type === "assistant_progress").map((item) => item.content)).toEqual(["分析一", "分析二"]);
    expect(groupAgentSteps(run.steps).map((group) => group.kind)).toEqual(["reasoning", "reasoning"]);
    expect(run.liveAnswer).toBe("行动说明");
    const view = render(<AgentExecution run={run} />);
    expect(view.container.querySelectorAll(".assistant-reasoning")).toHaveLength(2);
    expect(screen.getByText("思考过程 · 正在生成")).toBeTruthy();
    expect(screen.getByText("分析一")).toBeTruthy();
    const completed = buildAgentRun(assistant({ status: "completed", content: "回答", assistantSteps: [progressStep({ is_final: true, content_blocks: blocks })] }));
    expect(groupAgentSteps(completed.steps).map((group) => group.kind)).toEqual(["reasoning", "reasoning"]);
    expect(completed.finalAnswer).toBe("回答");
    view.rerender(<AgentExecution run={completed} />);
    expect(view.container.querySelectorAll("details.assistant-reasoning")).toHaveLength(2);
  });

  it.each(["streaming", "completed"] as const)("omits empty reasoning for a %s answer", (status) => {
    const run = buildAgentRun(assistant({ status, content: "你好", assistantSteps: [progressStep({ is_final: true, content: "你好" })] }));
    const { container } = render(<AgentExecution run={run} />);
    expect(container.querySelector(".assistant-reasoning")).toBeNull();
  });

  it("omits whitespace-only reasoning", () => {
    const run = buildAgentRun(assistant({ status: "completed", assistantSteps: [progressStep({ content_blocks: [{ type: "reasoning", text: " \n " }] })] }));
    const { container } = render(<AgentExecution run={run} />);
    expect(container.querySelector(".assistant-reasoning")).toBeNull();
  });

  it("keeps adjacent reasoning blocks from one model step in one box", () => {
    const run = buildAgentRun(assistant({ status: "completed", assistantSteps: [progressStep({
      content_blocks: [
        { type: "reasoning", text: "先分析" }, { type: "reasoning", text: "再确认" },
      ],
    })] }));
    const { container } = render(<AgentExecution run={run} />);
    expect(container.querySelectorAll(".assistant-reasoning")).toHaveLength(1);
    expect(container.querySelector(".assistant-reasoning")?.textContent).toContain("先分析再确认");
  });

  it("keeps typed deltas ordered through completion and timing reconciliation", () => {
    let state = { ...INITIAL_CHAT_STATE, messages: [assistant({ status: "streaming", assistantSteps: [progressStep()] })] };
    state = reducer(state, { type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "分析", contentKind: "reasoning" });
    state = reducer(state, { type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "说明", contentKind: "text" });
    expect(state.messages[0].assistantSteps[0].content_blocks).toEqual([
      { type: "reasoning", text: "分析" }, { type: "text", text: "说明" },
    ]);
    state = reducer(state, { type: "runActivity", activity: { type: "run_activity", id: "model", kind: "model", status: "started" } });
    state = reducer(state, { type: "runActivity", activity: { type: "run_activity", id: "model", kind: "model", status: "completed", duration_ms: 200, first_reasoning_ms: 10 } });
    expect(state.messages[0].timings?.activities).toHaveLength(1);
    const snapshot = state.messages[0].assistantSteps;
    state = reducer(state, { type: "completed", messageId: "a1", content: "说明", assistantSteps: snapshot, timings: { preparation_ms: 30, activities: state.messages[0].timings!.activities } });
    expect(state.messages[0].timings?.preparation_ms).toBe(30);
    expect(state.messages[0].assistantSteps[0].content_blocks).toHaveLength(2);
  });
  it("keeps progress text and tool calls in causal order inside one run", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        content: "先看看项目结构",
        status: "completed",
        tool_calls: [
          { call_id: "c1", name: "search_code", batch_index: 0, status: "completed", args_preview: '{"query":"AgentMessage"}' },
          { call_id: "c2", name: "read_file", batch_index: 1, status: "completed", args_preview: '{"path":"src/Message.tsx"}' },
        ],
      }),
      progressStep({ id: "run:step:1", ordinal: 1, content: "正在整理建议……", status: "streaming" }),
    ];
    const run = buildAgentRun(assistant({ assistantSteps: steps }));

    expect(run.status).toBe("running");
    expect(run.steps.map((step) => step.id)).toEqual([
      "run:step:0:text:0",
      "run:step:0:tool:c1",
      "run:step:0:tool:c2",
    ]);
    expect(run.liveAnswer).toBe("正在整理建议……");
    expect(run.finalAnswer).toBeNull();
  });

  it("merges tool results by call_id even for repeated names and out-of-order completion", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        tool_calls: [
          { call_id: "c1", name: "search_code", batch_index: 0, status: "completed", result_preview: "第一个结果" },
          { call_id: "c2", name: "search_code", batch_index: 1, status: "completed", result_preview: "第二个结果" },
        ],
      }),
    ];
    const run = buildAgentRun(assistant({ assistantSteps: steps }));
    const tools = run.steps.filter((step): step is ToolCallStep => step.type === "tool_call");

    expect(tools).toHaveLength(2);
    expect(tools.map((tool) => tool.resultPreview)).toEqual(["第一个结果", "第二个结果"]);
    expect(tools.map((tool) => tool.durationMs)).toEqual([null, null]);
  });

  it("computes tool duration only when both server timestamps exist", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        tool_calls: [
          { call_id: "c1", name: "read_file", batch_index: 0, status: "completed", started_at: 1_000, completed_at: 2_200 },
          { call_id: "c2", name: "read_file", batch_index: 1, status: "completed", completed_at: 2_200 },
        ],
      }),
    ];
    const tools = buildAgentRun(assistant({ assistantSteps: steps })).steps
      .filter((step): step is ToolCallStep => step.type === "tool_call");

    expect(tools[0].durationMs).toBe(1_200);
    expect(tools[1].durationMs).toBeNull();
  });

  it("separates only the backend-flagged final step from the execution trace", () => {
    const steps: AssistantStep[] = [
      progressStep({ id: "run:step:0", status: "completed", content: "中间说明" }),
      progressStep({
        id: "run:step:1",
        ordinal: 1,
        status: "completed",
        content: "最终答复",
        is_final: true,
      }),
    ];
    const run = buildAgentRun(
      assistant({ status: "completed", content: "最终答复", assistantSteps: steps, executionDurationMs: 16_400 }),
    );

    expect(run.status).toBe("completed");
    expect(run.steps.map((step) => step.id)).toEqual(["run:step:0:text:0"]);
    expect(run.finalAnswer).toBe("最终答复");
    expect(run.durationMs).toBe(16_400);
  });

  it("shows the current tool-free message as a provisional answer", () => {
    const steps: AssistantStep[] = [progressStep({ id: "run:step:0", content: "我先说明一下思路" })];
    const run = buildAgentRun(assistant({ assistantSteps: steps }));

    expect(run.finalAnswer).toBeNull();
    expect(run.liveAnswer).toBe("我先说明一下思路");
    expect(run.steps).toHaveLength(0);
  });

  it("returns provisional text to the timeline when the same step declares a tool", () => {
    const step = progressStep({ content: "先核对数据" });
    expect(buildAgentRun(assistant({ assistantSteps: [step] })).liveAnswer).toBe("先核对数据");
    const withTool = progressStep({ ...step, tool_calls: [{ call_id: "c1", name: "read_file", batch_index: 0, status: "running" }] });
    const run = buildAgentRun(assistant({ assistantSteps: [withTool] }));
    expect(run.liveAnswer).toBeNull();
    expect(run.steps.map((item) => item.type)).toEqual(["assistant_progress", "tool_call"]);
  });

  it("marks a pending history message as unconfirmed instead of running", () => {
    const run = buildAgentRun(assistant({ status: "pending" }));

    expect(run.status).toBe("unconfirmed");
    expect(run.finalAnswer).toBeNull();
  });

  it("falls back to local timestamps when the server sends no duration", () => {
    const run = buildAgentRun(
      assistant({ status: "completed", content: "答复", startedAt: 1_000, completedAt: 4_300 }),
    );

    expect(run.durationMs).toBe(3_300);
  });

  it("reports no duration at all when there is no reliable time information", () => {
    const run = buildAgentRun(assistant({ status: "completed", content: "答复", startedAt: null }));

    expect(run.durationMs).toBeNull();
    expect(formatDuration(run.durationMs)).toBeNull();
  });
});

describe("agent step grouping", () => {
  it("renders tool-bearing AIMessage text before results and updates it while the tool is running", async () => {
    let state = reducer({ ...INITIAL_CHAT_STATE, messages: [assistant()] }, {
      type: "assistantStepStarted", messageId: "a1", step: progressStep(),
    });
    state = reducer(state, {
      type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "我先检索。",
    });
    const view = render(<AgentExecution run={buildAgentRun(state.messages[0])} />);
    expect(buildAgentRun(state.messages[0]).liveAnswer).toBe("我先检索。");
    expect(view.container.querySelector(".agent-execution-body")).toBeNull();
    state = reducer(state, {
      type: "assistantToolCall", messageId: "a1", stepId: "run:step:0",
      call: { call_id: "probe", name: "internet_search", batch_index: 0, status: "running" },
    });
    state = reducer(state, {
      type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "正在组织检索参数。",
    });
    view.rerender(<AgentExecution run={buildAgentRun(state.messages[0])} />);
    expect(await screen.findByText("我先检索。正在组织检索参数。")).toBeTruthy();
    expect(state.messages[0].assistantSteps[0].tool_calls[0].status).toBe("running");
    expect(view.container.querySelector(".agent-execution-body")).not.toBeNull();
  });

  it("renders each tool once and keeps its details expandable", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        tool_calls: [
          { call_id: "c1", name: "ls", batch_index: 0, status: "completed", args_preview: '{"path":"/"}' },
          { call_id: "c2", name: "grep", batch_index: 1, status: "completed", args_preview: '{"pattern":"README"}' },
        ],
      }),
    ];
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ status: "completed", assistantSteps: steps }))} />,
    );
    expect(container.querySelectorAll(".agent-tool")).toHaveLength(2);
    expect(container.querySelectorAll(".agent-tool summary")[0].textContent).toContain("查看目录");
    expect(container.querySelectorAll(".agent-tool summary")[1].textContent).toContain("查找内容");
    expect((container.querySelector(".agent-tool") as HTMLDetailsElement).open).toBe(false);

    // jsdom 不会因点击 summary 自行切换 details，这里直接模拟原生 toggle。
    const firstTool = container.querySelector(".agent-tool") as HTMLDetailsElement;
    firstTool.open = true;
    fireEvent(firstTool, new Event("toggle", { bubbles: true }));
    expect(firstTool.querySelector(".agent-tool-details")).not.toBeNull();
  });

  it("keeps text and tool groups in causal order across steps", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        content: "先看看目录",
        tool_calls: [{ call_id: "c1", name: "ls", batch_index: 0, status: "completed" }],
      }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "streaming", content: "正在整理结论……" }),
    ];
    const groups = groupAgentSteps(buildAgentRun(assistant({ assistantSteps: steps })).steps);

    expect(groups.map((group) => group.kind)).toEqual(["text", "tools"]);
  });

  it("keeps groups from different steps separate", () => {
    const steps: AssistantStep[] = [
      progressStep({ id: "run:step:0", status: "completed", tool_calls: [{ call_id: "c1", name: "ls", batch_index: 0, status: "completed" }] }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", tool_calls: [{ call_id: "c2", name: "ls", batch_index: 0, status: "completed" }] }),
    ];
    const groups = groupAgentSteps(buildAgentRun(assistant({ assistantSteps: steps })).steps);

    expect(groups).toHaveLength(2);
    expect(groups.every((group) => group.kind === "tools")).toBe(true);
  });

  it("expands a failed tool so the failure is visible without extra clicks", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "failed",
        tool_calls: [{ call_id: "c1", name: "execute", batch_index: 0, status: "failed", error: "命令失败" }],
      }),
    ];
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ status: "failed", assistantSteps: steps }))} />,
    );

    expect(container.querySelector(".agent-tool.is-failed")).not.toBeNull();
    expect(container.querySelectorAll(".agent-tool")).toHaveLength(1);
    expect(container.querySelector(".agent-tool-details")).not.toBeNull();
  });
});

describe("agent tool summary", () => {
  const toolStep = (tools: ToolCallStep["toolName"][]): AssistantStep => ({
    id: "run:step:0",
    ordinal: 0,
    content: "",
    status: "completed",
    is_final: false,
    tool_calls: tools.map((name, index) => ({
      call_id: `c${index}`,
      name,
      batch_index: index,
      status: "completed",
    })),
  });

  it("summarises distinct tool labels and keeps counting duplicates", () => {
    const steps = buildAgentRun(
      assistant({ status: "completed", assistantSteps: [toolStep(["read_file", "read_file", "grep", "ls", "glob", "task"])] }),
    ).steps;

    expect(summariseAgentTools(steps)).toMatchObject({
      count: 6,
      names: ["读取文件", "查找内容", "查看目录"],
      more: 2,
    });
  });

  it("returns null when the run only produced text", () => {
    const steps = buildAgentRun(assistant({ assistantSteps: [progressStep({ content: "只有文本" })] })).steps;

    expect(summariseAgentTools(steps)).toBeNull();
  });
});

describe("duration formatting", () => {
  it("formats seconds and minutes and rejects unknown values", () => {
    expect(formatDuration(8_300)).toBe("8.3s");
    expect(formatDuration(12_800)).toBe("12.8s");
    expect(formatDuration(68_000)).toBe("1m 08s");
    // 8ms 的真实耗时不能被四舍五入成看着像没有数据的 0.0s。
    expect(formatDuration(8)).toBe("<0.1s");
    expect(formatDuration(null)).toBeNull();
  });
});

describe("tool call summary", () => {
  it("prefers the most relevant argument and falls back to the raw first line", () => {
    expect(toolCallSummary('{"path":"src/Message.tsx"}')).toBe("src/Message.tsx");
    expect(toolCallSummary('{"command":"npm test"}')).toBe("npm test");
    expect(toolCallSummary("不是 JSON 的参数")).toBe("不是 JSON 的参数");
    expect(toolCallSummary("")).toBeNull();
  });
});

describe("agent execution panel", () => {
  it("keeps total duration without timing details or an empty toggle for a plain answer", () => {
    const run = buildAgentRun(assistant({ status: "completed", content: "你好", assistantSteps: [progressStep({ is_final: true, content: "你好" })],
      executionDurationMs: 1700,
      timings: { preparation_ms: 1100, activities: [{ type: "run_activity", id: "model", kind: "model", status: "completed", duration_ms: 600, first_text_ms: 500 }] } }));
    const { container } = render(<AgentExecution run={run} />);
    expect(screen.getByText("· 耗时 1.7s")).toBeTruthy();
    expect(screen.queryByText("查看耗时")).toBeNull();
    expect(container.querySelector(".agent-run-timings")).toBeNull();
    expect(container.querySelector(".assistant-reasoning")).toBeNull();
    expect((screen.getByRole("button", { name: "执行状态，完成" }) as HTMLButtonElement).disabled).toBe(true);
    expect(container.querySelector(".agent-execution-chevron")).toBeNull();
  });

  it("places each reasoning segment around its tool and reconciles the final snapshot", () => {
    const beforeTool = progressStep({
      id: "run:step:0", content: "先分析", content_blocks: [{ type: "reasoning", text: "先分析" }],
      tool_calls: [{ call_id: "c1", name: "read_file", batch_index: 0, status: "running" }],
    });
    const { container, rerender } = render(<AgentExecution run={buildAgentRun(assistant({ assistantSteps: [beforeTool] }))} />);
    expect(Array.from(container.querySelector(".agent-timeline")!.children).map((node) => node.className)).toEqual([
      "assistant-reasoning", "agent-tool-list",
    ]);
    const afterTool = progressStep({
      id: "run:step:1", ordinal: 1, content: "再分析", content_blocks: [{ type: "reasoning", text: "再分析" }],
    });
    rerender(<AgentExecution run={buildAgentRun(assistant({ assistantSteps: [
      { ...beforeTool, status: "completed", tool_calls: [{ ...beforeTool.tool_calls[0], status: "completed" }] },
      afterTool,
    ] }))} />);
    expect(Array.from(container.querySelector(".agent-timeline")!.children).map((node) => node.className)).toEqual([
      "assistant-reasoning", "agent-tool-list", "assistant-reasoning",
    ]);
    expect(container.querySelectorAll(".assistant-reasoning")).toHaveLength(2);
    expect(container.querySelectorAll(".assistant-reasoning-title")).toHaveLength(2);
    expect(container.querySelectorAll(".assistant-reasoning-title")[0].textContent).toBe("思考过程");
    expect(container.querySelectorAll(".assistant-reasoning-title")[1].textContent).toBe("思考过程 · 正在生成");

    const finished = buildAgentRun(assistant({ status: "completed", content: "最终答复", assistantSteps: [
      { ...beforeTool, status: "completed", tool_calls: [{ ...beforeTool.tool_calls[0], status: "completed" }] },
      { ...afterTool, status: "completed", is_final: true, content_blocks: [
        { type: "reasoning", text: "再分析" }, { type: "text", text: "最终答复" },
      ] },
    ] }));
    rerender(<AgentExecution run={finished} />);
    const parts = container.querySelectorAll<HTMLDetailsElement>("details.assistant-reasoning");
    expect(parts).toHaveLength(2);
    expect(Array.from(container.querySelector(".agent-timeline")!.children).map((node) => node.className)).toEqual([
      "assistant-reasoning", "agent-tool-list", "assistant-reasoning",
    ]);
    expect(parts[0].open).toBe(false);
    expect(parts[1].open).toBe(false);
    fireEvent.click(parts[1].querySelector("summary")!);
    expect(parts[0].open).toBe(false);
    expect(parts[1].open).toBe(true);
    expect(parts[1].textContent).toContain("再分析");
    expect(parts[1].textContent).not.toContain("最终答复");
    expect(finished.finalAnswer).toBe("最终答复");
  });

  it("keeps final-step reasoning inside the execution timeline", () => {
    const run = buildAgentRun(assistant({ status: "completed", content: "最终答复", assistantSteps: [
      progressStep({ status: "completed", content: "中间说明" }),
      progressStep({ id: "run:step:1", ordinal: 1, is_final: true, status: "completed", content_blocks: [{ type: "reasoning", text: "分析内容" }, { type: "text", text: "最终答复" }] }),
    ] }));
    const { container } = render(<AgentExecution run={run} />);
    const panel = container.querySelector(".agent-execution")!;
    const reasoning = panel.querySelector("details.assistant-reasoning") as HTMLDetailsElement;
    expect(panel.children[0].className).toBe("agent-execution-head");
    expect(panel.children[1].className).toBe("agent-execution-body");
    expect(panel.querySelector(".agent-timeline")?.lastElementChild).toBe(reasoning);
    expect(reasoning.open).toBe(false);
    fireEvent.click(screen.getByText("思考过程"));
    expect(reasoning.open).toBe(true);
    expect(reasoning.textContent).toContain("分析内容");
    expect(reasoning.textContent).not.toContain("最终答复");
    fireEvent.click(screen.getByRole("button", { name: /完成/ }));
    expect(container.querySelector(".agent-execution-body")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /完成/ }));
    expect(container.querySelector("details.assistant-reasoning")).not.toBeNull();
  });
  it("forces the running execution open and blocks collapsing", () => {
    const steps: AssistantStep[] = [progressStep({ id: "run:step:0", content: "分析项目结构", tool_calls: [{ call_id: "c1", name: "read_file", batch_index: 0, status: "running" }] })];
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ assistantSteps: steps }))} />,
    );
    const head = screen.getByRole("button", { name: /正在读取文件/ }) as HTMLButtonElement;

    expect(head.disabled).toBe(true);
    expect(head.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector(".agent-execution-body")).not.toBeNull();

    act(() => { head.click(); });
    expect(head.getAttribute("aria-expanded")).toBe("true");
  });

  it("collapses a finished run and lets the user expand it again", () => {
    const steps: AssistantStep[] = [
      progressStep({ id: "run:step:0", status: "completed", content: "中间说明" }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
    ];
    const message = assistant({
      status: "completed",
      content: "最终答复",
      assistantSteps: steps,
      executionDurationMs: 16_400,
    });
    const streaming = { ...message, status: "streaming" as const };
    const { container, rerender } = render(<AgentExecution run={buildAgentRun(streaming)} />);
    rerender(<AgentExecution run={buildAgentRun(message)} />);
    const head = screen.getByRole("button", { name: /完成/ }) as HTMLButtonElement;

    expect(head.disabled).toBe(false);
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".agent-execution-body")).toBeNull();

    act(() => { head.click(); });
    expect(container.querySelector(".agent-execution-body")).not.toBeNull();

    // 重复的完成事件或普通 rerender 不能覆盖用户手动展开的选择。
    rerender(<AgentExecution run={buildAgentRun({ ...message, assistantSteps: [...steps] })} />);
    expect(screen.getByRole("button", { name: /完成/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps an already completed history run expandable", () => {
    const steps: AssistantStep[] = [
      progressStep({ id: "run:step:0", status: "completed", content: "中间说明" }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
    ];
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ status: "completed", content: "最终答复", assistantSteps: steps }))} />,
    );

    expect(screen.getByRole("button", { name: /完成/ }).getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".agent-execution-body")).toBeNull();

    act(() => {
      (screen.getByRole("button", { name: /完成/ }) as HTMLButtonElement).click();
    });
    expect(container.querySelector(".agent-execution-body")).not.toBeNull();
  });

  it("shows the execution area as soon as the message is sent", () => {
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ status: "streaming", phases: ["responding"] }))} />,
    );
    const head = screen.getByRole("button", { name: /正在生成回复|正在准备/ }) as HTMLButtonElement;

    expect(head.disabled).toBe(true);
    expect(container.querySelector(".agent-execution-body")).toBeNull();
  });

  it("renders nothing when the message has no execution steps", () => {
    const { container } = render(
      <AgentExecution
        run={buildAgentRun(assistant({ status: "completed", content: "很久以前的答复" }))}
        events={[
          { type: "model_usage", call_id: "main", kind: "main", input_tokens: 10, output_tokens: 4 },
          { type: "context_usage", scope: "main", estimated_input_tokens: 100 },
        ]}
      />,
    );

    expect(container.querySelector(".agent-execution")).toBeNull();
  });

  it("keeps tools visible after completion and history reload, with separate details for repeated names", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        tool_calls: [
          { call_id: "c1", name: "read_file", batch_index: 0, status: "completed", args_preview: '{"path":"first.txt"}', result_preview: "第一个结果" },
          { call_id: "c2", name: "read_file", batch_index: 1, status: "completed", args_preview: '{"path":"second.txt"}', result_preview: "第二个结果" },
        ],
      }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
    ];
    const message = assistant({ status: "completed", content: "最终答复", assistantSteps: steps });
    const { container, rerender, unmount } = render(<AgentExecution run={buildAgentRun({ ...message, status: "streaming" })} />);
    rerender(<AgentExecution run={buildAgentRun(message)} />);
    expect(screen.getByRole("button", { name: /完成/ }).getAttribute("aria-expanded")).toBe("true");
    const tools = container.querySelectorAll<HTMLDetailsElement>("details.agent-tool");
    expect(tools).toHaveLength(2);
    expect(Array.from(tools).map((tool) => tool.querySelector("summary")?.textContent)).toEqual([
      expect.stringContaining("first.txt"), expect.stringContaining("second.txt"),
    ]);
    expect(tools[0].open).toBe(false);
    fireEvent.click(tools[0].querySelector("summary")!);
    expect(tools[0].open).toBe(true);
    expect(tools[1].open).toBe(false);
    expect(tools[0].textContent).toContain("第一个结果");
    expect(tools[0].textContent).not.toContain("第二个结果");
    fireEvent.click(screen.getByRole("button", { name: /完成/ }));
    rerender(<AgentExecution run={buildAgentRun({ ...message, assistantSteps: [...steps] })} />);
    expect(container.querySelector(".agent-execution-body")).toBeNull();
    unmount();
    const history = render(<AgentExecution run={buildAgentRun(message)} />);
    expect(history.container.querySelectorAll(".agent-tool")).toHaveLength(2);
  });

  it("keeps a completed subagent visible when there are no root tool steps", () => {
    const { container } = render(<AgentExecution
      run={buildAgentRun(assistant({ status: "completed", content: "最终答复" }))}
      events={[
        { type: "subagent_started", subagent_id: "child", subagent_name: "分析助手" },
        { type: "subagent_text", subagent_id: "child", text: "分析已完成" },
        { type: "subagent_completed", subagent_id: "child" },
      ]}
      messageStatus="completed"
    />);
    expect(screen.getByRole("button", { name: /完成/ }).getAttribute("aria-expanded")).toBe("true");
    const node = container.querySelector<HTMLDetailsElement>(".agent-subagent")!;
    expect(node.querySelector("summary")?.textContent).toContain("分析助手");
    expect(node.open).toBe(false);
    fireEvent.click(node.querySelector("summary")!);
    expect(node.open).toBe(true);
    expect(node.textContent).toContain("分析已完成");
  });

  it("keeps failed runs expanded so the failure point stays visible", () => {
    const steps: AssistantStep[] = [
      progressStep({ id: "run:step:0", status: "failed", content: "读取失败" }),
    ];
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ status: "failed", assistantSteps: steps }))} />,
    );

    expect(container.querySelector(".agent-execution-body")).not.toBeNull();
    expect(container.querySelector(".agent-execution.is-failed")).not.toBeNull();
  });

  it("renders the final answer outside the execution container", () => {
    const steps: AssistantStep[] = [
      progressStep({ id: "run:step:0", status: "completed", content: "中间说明" }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
    ];
    const run = buildAgentRun(
      assistant({ status: "completed", content: "最终答复", assistantSteps: steps }),
    );
    const { container } = render(<AgentExecution run={run} />);
    act(() => {
      (screen.getByRole("button", { name: /完成/ }) as HTMLButtonElement).click();
    });
    const panel = container.querySelector(".agent-execution") as HTMLElement;

    expect(run.finalAnswer).toBe("最终答复");
    expect(panel.textContent).toContain("中间说明");
    expect(panel.textContent).not.toContain("最终答复");
  });
});

describe("streaming reducer updates", () => {
  it("keeps waiting until visible text arrives instead of treating step start as output", () => {
    let state = { ...INITIAL_CHAT_STATE, messages: [assistant({ phases: ["waiting_model"] })] };
    state = reducer(state, { type: "assistantStepStarted", messageId: "a1", step: progressStep() });
    expect(state.messages[0].phases.at(-1)).toBe("waiting_model");
    state = reducer(state, { type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "正文" });
    expect(state.messages[0].phases.at(-1)).toBe("responding");
  });

  it("appends progress deltas into the same step instead of creating new entries", () => {
    let state = { ...INITIAL_CHAT_STATE, messages: [assistant()] };
    state = reducer(state, { type: "assistantStepStarted", messageId: "a1", step: progressStep() });
    state = reducer(state, { type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "分析" });
    state = reducer(state, { type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "项目结构" });

    expect(state.messages[0].assistantSteps).toHaveLength(1);
    expect(state.messages[0].assistantSteps?.[0].content).toBe("分析项目结构");
  });

  it("does not append a replayed snapshot twice", () => {
    let state = { ...INITIAL_CHAT_STATE, messages: [assistant()] };
    state = reducer(state, {
      type: "assistantStepStarted",
      messageId: "a1",
      step: progressStep({ content: "已完成的说明", status: "completed" }),
    });
    state = reducer(state, { type: "assistantTextDelta", messageId: "a1", stepId: "run:step:0", delta: "已完成的说明" });

    expect(state.messages[0].assistantSteps?.[0].content).toBe("已完成的说明");
  });

  it("excludes approval waiting time when resuming the live clock", () => {
    const clock = vi.spyOn(Date, "now").mockReturnValue(100_000);
    try {
      const state = reducer({ ...INITIAL_CHAT_STATE, messages: [assistant({ status: "interrupted", startedAt: 1_000,
        executionDurationMs: 4_000, timings: { preparation_ms: 10, activities: [] } })] },
      { type: "messageStarted", assistantMessageId: "a1", userMessageId: null, resuming: true, preparationDurationMs: 5 });
      expect(state.messages[0].startedAt).toBe(96_000);
      expect(state.messages[0].timings?.preparation_ms).toBe(15);
    } finally { clock.mockRestore(); }
  });

  it("ignores late events that would revive a finished run", () => {
    let state = reducer(
      { ...INITIAL_CHAT_STATE, messages: [assistant()] },
      { type: "completed", messageId: "a1", content: "答复", assistantSteps: [] },
    );
    state = reducer(state, {
      type: "messageStarted",
      assistantMessageId: "a1",
      userMessageId: null,
    });
    state = reducer(state, { type: "text", text: "迟到的增量" });
    state = reducer(state, {
      type: "assistantStepStarted",
      messageId: "a1",
      step: progressStep({ id: "run:step:9", ordinal: 9 }),
    });

    expect(state.messages[0].status).toBe("completed");
    expect(state.messages[0].content).toBe("答复");
    expect(state.messages[0].assistantSteps).toHaveLength(0);
  });

  it("uses the final-step flag from the completed snapshot", () => {
    const state = reducer(
      {
        ...INITIAL_CHAT_STATE,
        messages: [
          assistant({
            assistantSteps: [
              progressStep({ id: "run:step:0", status: "completed", content: "中间说明" }),
              progressStep({ id: "run:step:1", ordinal: 1, status: "streaming", content: "最终答复" }),
            ],
          }),
        ],
      },
      {
        type: "completed",
        messageId: "a1",
        content: "最终答复",
        assistantSteps: [
          progressStep({ id: "run:step:0", status: "completed", content: "中间说明" }),
          progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
        ],
      },
    );

    expect(state.messages[0].assistantSteps?.map((step) => step.is_final)).toEqual([false, true]);
    expect(state.messages[0].assistantSteps?.[0].content).toBe("中间说明");
  });

  it("reconciles missing live steps from the completed server snapshot", () => {
    const snapshot = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        content: "中间说明",
        tool_calls: [{ call_id: "c1", name: "read_file", batch_index: 0, status: "completed" }],
      }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
    ];
    const state = reducer(
      { ...INITIAL_CHAT_STATE, messages: [assistant({ assistantSteps: [] })] },
      {
        type: "completed",
        messageId: "a1",
        content: "最终答复",
        assistantSteps: snapshot,
      },
    );

    expect(state.messages[0].assistantSteps).toHaveLength(2);
    expect(buildAgentRun(state.messages[0]).steps.map((step) => step.type)).toEqual([
      "assistant_progress",
      "tool_call",
    ]);
  });

  it("stops the clock when the run reaches a terminal status", () => {
    const state = reducer(
      { ...INITIAL_CHAT_STATE, messages: [assistant()] },
      { type: "messageStatus", messageId: "a1", status: "cancelled" },
    );
    const run = buildAgentRun(state.messages[0]);

    expect(run.status).toBe("cancelled");
    expect(typeof state.messages[0].completedAt).toBe("number");
    expect(run.steps).toHaveLength(0);
  });
});

describe("run clock", () => {
  it("only ticks while the run is active and stops on unmount", () => {
    vi.useFakeTimers();
    try {
      const { container, unmount } = render(
        <AgentExecution run={buildAgentRun(assistant({ startedAt: Date.now() }))} />,
      );
      expect(container.querySelector(".agent-execution-duration")?.textContent).toContain("0.0s");
      act(() => { vi.advanceTimersByTime(1_300); });
      expect(container.querySelector(".agent-execution-duration")?.textContent).toContain("1.3s");
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
