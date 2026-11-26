import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ComplianceStatement, FilingFooter } from "../src/components/ComplianceNotice";

it("备案号链接工信部首页", () => {
  render(<FilingFooter />);
  const link = screen.getByRole("link", { name: "京ICP备2026082777号-1" });
  expect(link.getAttribute("href")).toBe("https://beian.miit.gov.cn/");
});

it("闲置声明组件仍可单独渲染", () => {
  render(<ComplianceStatement />);
  expect(screen.getByRole("contentinfo").textContent).toContain("本站已完成ICP备案");
  expect(screen.getByRole("contentinfo").querySelectorAll("p")).toHaveLength(4);
});
