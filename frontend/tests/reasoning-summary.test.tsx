import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReasoningSummary, buildReasoningSteps } from "../src/components/ReasoningSummary";

describe("reasoning summary", () => {
  it("summarizes phases and tools without exposing hidden reasoning", () => {
    const steps = buildReasoningSteps(
      ["starting", "thinking", "responding"],
      [{ type: "tool_call", name: "search_web", call_key: "a" }],
      "completed",
    );
    expect(steps.map((step) => step.label)).toEqual([
      "准备执行",
      "组织回答思路",
      "生成回复",
      "已调用相关工具",
      "回复已生成",
    ]);
    expect(steps.some((step) => step.label.includes("think"))).toBe(false);
  });

  it("is collapsed after completion and expands on demand", () => {
    const { container } = render(
      <ReasoningSummary
        phases={["thinking", "responding"]}
        events={[]}
        status="completed"
      />,
    );
    const details = container.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(screen.getByText("思路摘要"));
    expect(details.open).toBe(true);
    expect(screen.getByText("组织回答思路")).toBeTruthy();
  });
});
