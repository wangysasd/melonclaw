import { describe, expect, it } from "vitest";
import { mergeDisplayEvent, summarizeModelUsage } from "../src/lib/modelUsage";

describe("model usage", () => {
  it("merges start/end and resumed call ids without doubling totals", () => {
    const start = { type: "model_usage", call_id: "one", kind: "main", status: "started", input_tokens: null, output_tokens: null };
    let events = mergeDisplayEvent([], start);
    events = mergeDisplayEvent(events, { ...start, status: "completed", input_tokens: 10, output_tokens: 4 });
    events = mergeDisplayEvent(events, { ...start, status: "completed", input_tokens: 10, output_tokens: 4 });
    expect(summarizeModelUsage(events)[0]).toMatchObject({ calls: 1, reported: 1, input: 10, output: 4 });
  });
  it("keeps unknown reports distinct from zero tokens and includes failed attempts", () => {
    const events = [
      { type: "model_usage", call_id: "one", kind: "main", status: "failed", input_tokens: null, output_tokens: null },
      { type: "model_usage", call_id: "two", kind: "main", status: "completed", input_tokens: 0, output_tokens: 0 },
    ];
    expect(summarizeModelUsage(events)[0]).toMatchObject({ calls: 2, reported: 1, failed: 1, input: 0 });
    expect(summarizeModelUsage([events[0]])[0].reported).toBe(0);
  });
});
