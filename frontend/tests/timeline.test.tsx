import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { buildTimeline, ToolTimeline } from "../src/components/ToolTimeline";

describe("tool activity", () => {
  it("matches anonymous results to their call instead of adding a second running card", () => {
    const nodes = buildTimeline([{ type: "tool_call", name: "search" }, { type: "tool_result", name: "search", content: "ok" }]);
    expect(nodes).toHaveLength(1); expect(nodes[0].status).toBe("completed");
  });
  it("counts unique subagent calls despite repeated progress events", () => {
    const nodes = buildTimeline([{ type: "subagent_started", subagent_id: "a" }, ...[1, 2].map(() => ({ type: "subagent_tool_call", subagent_id: "a", call_key: "k", name: "search" }))]);
    expect(nodes[0]).toMatchObject({ kind: "subagent", toolCount: 1 });
  });
  it("never keeps a tool spinning after the run pauses or ends without a result", () => {
    const events = [{ type: "tool_call", name: "execute", call_key: "a" }];
    expect(buildTimeline(events, "interrupted")[0].status).toBe("waiting");
    expect(buildTimeline(events, "failed")[0].status).toBe("unknown");
    expect(buildTimeline(events, "completed")[0].status).toBe("unknown");
  });
  it("keeps routine details collapsed and reveals failures", () => {
    const events = [{ type: "tool_call", name: "execute", call_key: "a", args: { command: "echo hello" } }];
    const { container, rerender } = render(<ToolTimeline events={events} messageStatus="streaming" />);
    expect(container.querySelector("details")?.open).toBe(false);
    rerender(<ToolTimeline events={[...events, { type: "tool_result", call_key: "a", status: "failed", content: "无法执行" }]} messageStatus="failed" />);
    expect(container.querySelector("details")?.open).toBe(true);
    expect(screen.getByText("错误详情")).toBeTruthy();
  });
});
