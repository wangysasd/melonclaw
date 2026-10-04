import { describe, expect, it } from "vitest";

import {
  TOOL_CATALOG,
  TOOL_CATEGORIES,
  toolDefinition,
  toolIconName,
} from "../src/lib/toolCatalog";

describe("tool catalog", () => {
  it("lists every fixed Agent tool exactly once", () => {
    expect(TOOL_CATALOG).toHaveLength(25);
    expect(new Set(TOOL_CATALOG.map((tool) => tool.name)).size).toBe(25);
    expect(new Set(TOOL_CATALOG.map((tool) => tool.category))).toEqual(
      new Set(TOOL_CATEGORIES.map((category) => category.id)),
    );
  });

  it("uses canonical tool names", () => {
    expect(toolDefinition("delete")?.name).toBe("delete");
    expect(toolDefinition("internet_search")?.name).toBe("internet_search");
    expect(toolDefinition("delete_file")).toBeUndefined();
    expect(toolDefinition("tavily_search")).toBeUndefined();
  });

  it("keeps semantic icons consistent for known and unknown tools", () => {
    expect(toolIconName("execute")).toBe("terminal");
    expect(toolIconName("internet_search")).toBe("globe-2");
    expect(toolIconName("unregistered_mcp_tool")).toBe("wrench");
  });
});
