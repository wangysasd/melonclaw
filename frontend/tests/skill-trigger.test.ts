import { describe, expect, it } from "vitest";
import { findSkillTrigger } from "../src/lib/skillTrigger";

describe("skill trigger", () => {
  it("recognizes a slash at the start or after whitespace", () => {
    expect(findSkillTrigger("/tusha", 6)).toEqual({
      start: 0,
      end: 6,
      query: "tusha",
    });
    expect(findSkillTrigger("请用 /tusha", 9)).toEqual({
      start: 3,
      end: 9,
      query: "tusha",
    });
  });

  it("does not recognize URL paths or a non-collapsed selection", () => {
    expect(findSkillTrigger("访问 https://example.com/", 23)).toBeNull();
    expect(findSkillTrigger("/skill", 2, 6)).toBeNull();
  });

  it("uses the caret to limit the active query", () => {
    expect(findSkillTrigger("/tushare data", 8)).toEqual({
      start: 0,
      end: 8,
      query: "tushare",
    });
    expect(findSkillTrigger("/tushare data", 13)).toBeNull();
  });
});
