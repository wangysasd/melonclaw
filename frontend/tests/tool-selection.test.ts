import { describe, expect, it } from "vitest";

import { classifyToolSelectorText } from "../src/lib/toolSelection";

describe("tool selector text", () => {
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
