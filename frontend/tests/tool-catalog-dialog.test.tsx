import { render, screen, within } from "@testing-library/react";
import { vi, expect, test } from "vitest";
import { ToolCatalogDialog } from "../src/components/ToolCatalogDialog";
vi.mock("../src/api/client", () => ({
  listSkills: async () => ({ items: [{ id: "skill", display_name: "研究技能", description: "研究资料", scope: "global" }] }),
  listMcp: async () => ({ items: [
    { id: "on", display_name: "启用服务", slug: "on", transport: "http", effective_enabled: true },
    { id: "off", display_name: "停用服务", slug: "off", transport: "http", effective_enabled: false },
  ] }),
}));
test("groups management tools and counts only enabled directories", async () => {
  render(<ToolCatalogDialog open userId="admin" status={null} modelOptions={[]} onClose={() => {}} />);
  await screen.findByText("研究技能");
  expect(screen.getAllByRole("heading", { level: 3 }).map(node => node.textContent)).toEqual(["模型", "工具", "技能", "MCP", "其他"]);
  const skills = screen.getByRole("region", { name: "技能" });
  expect(within(skills).getByText("安装 Skill")).toBeTruthy();
  expect(within(screen.getByRole("region", { name: "工具" })).queryByText("安装 Skill")).toBeNull();
  const summary = screen.getByLabelText("系统状态摘要");
  expect(summary.textContent).toContain("18 个固定工具");
  expect(summary.textContent).toContain("1 个启用技能");
  expect(summary.textContent).toContain("1 个启用 MCP");
  expect(screen.getByText("停用服务")).toBeTruthy();
});
