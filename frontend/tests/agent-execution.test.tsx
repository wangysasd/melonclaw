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
      "run:step:0:text",
      "run:step:0:tool:c1",
      "run:step:0:tool:c2",
      "run:step:1:text",
    ]);
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
    expect(run.steps.map((step) => step.id)).toEqual(["run:step:0:text"]);
    expect(run.finalAnswer).toBe("最终答复");
    expect(run.durationMs).toBe(16_400);
  });

  it("keeps a process message without tool calls inside the trace while streaming", () => {
    // 过程消息也可能没有任何工具调用：不能凭形状把它当成最终回答。
    const steps: AssistantStep[] = [progressStep({ id: "run:step:0", content: "我先说明一下思路" })];
    const run = buildAgentRun(assistant({ assistantSteps: steps }));

    expect(run.finalAnswer).toBeNull();
    expect(run.steps).toHaveLength(1);
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
    act(() => {
      (screen.getByRole("button", { name: /完成/ }) as HTMLButtonElement).click();
    });
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

    expect(groups.map((group) => group.kind)).toEqual(["text", "tools", "text"]);
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
  it("forces the running execution open and blocks collapsing", () => {
    const steps: AssistantStep[] = [progressStep({ id: "run:step:0", content: "分析项目结构" })];
    const { container } = render(
      <AgentExecution run={buildAgentRun(assistant({ assistantSteps: steps }))} />,
    );
    const head = screen.getByRole("button", { name: /正在生成回复|正在准备/ }) as HTMLButtonElement;

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
    expect(container.querySelector(".agent-step-pending")?.textContent).toContain("正在生成回复");
  });

  it("renders nothing when the message has no execution steps", () => {
    const { container } = render(
      <AgentExecution
        run={buildAgentRun(assistant({ status: "completed", content: "很久以前的答复" }))}
      />,
    );

    expect(container.querySelector(".agent-execution")).toBeNull();
  });

  it("keeps the called tools readable while the run is collapsed", () => {
    const steps: AssistantStep[] = [
      progressStep({
        id: "run:step:0",
        status: "completed",
        tool_calls: [
          { call_id: "c1", name: "read_file", batch_index: 0, status: "completed" },
          { call_id: "c2", name: "grep", batch_index: 1, status: "completed" },
        ],
      }),
      progressStep({ id: "run:step:1", ordinal: 1, status: "completed", content: "最终答复", is_final: true }),
    ];
    const { container } = render(
      <AgentExecution
        run={buildAgentRun(assistant({ status: "completed", content: "最终答复", assistantSteps: steps }))}
      />,
    );
    const head = container.querySelector(".agent-execution-head") as HTMLElement;

    expect(container.querySelector(".agent-execution-body")).toBeNull();
    // 收起状态也不能把"调用了哪些工具"藏起来。
    expect(head.textContent).not.toContain("已调用 2 个工具");
    expect(head.textContent).not.toContain("读取文件");
    expect(head.textContent).not.toContain("查找内容");
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
