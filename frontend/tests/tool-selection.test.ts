import { describe, expect, it } from "vitest";

import { classifyToolSelectorText, visibleAssistantText } from "../src/lib/toolSelection";

describe("tool selector text", () => {
  it("hides reasoning in saved assistant messages", () => {
    expect(visibleAssistantText('<think>internal</think>{"tools": []}')).toBe("");
    expect(visibleAssistantText("<think>internal</think>正文")).toBe("正文");
    expect(visibleAssistantText("<think>unfinished")).toBe("");
  });
  it("recognizes the complete internal selector JSON", () => {
    expect(classifyToolSelectorText('{"tools":[]}')).toBe("selector");
  });

  it("buffers a selector split across stream chunks", () => {
    expect(classifyToolSelectorText('{"tools":')).toBe("pending");
  });

  it("keeps ordinary JSON and prose visible", () => {
    expect(classifyToolSelectorText('{"answer":"ok"}')).toBe("text");
    expect(classifyToolSelectorText("正在整理答案")).toBe("text");
  });
});
