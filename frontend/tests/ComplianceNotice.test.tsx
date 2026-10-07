import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AccessNoticeGate, ComplianceStatement } from "../src/components/ComplianceNotice";

beforeEach(() => localStorage.clear());

describe("访问提示", () => {
  it("确认前不挂载站点，Escape 和遮罩不能跳过，确认后刷新记住选择", async () => {
    const contentMounted = vi.fn();
    function Content() { contentMounted(); return <div>站点内容</div>; }
    const view = render(<AccessNoticeGate><Content /></AccessNoticeGate>);
    expect(contentMounted).not.toHaveBeenCalled();
    expect(screen.queryByText("站点内容")).toBeNull();
    expect(screen.queryByLabelText("Close")).toBeNull();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape", keyCode: 27 });
    const mask = document.querySelector(".ant-modal-mask");
    if (mask) fireEvent.click(mask);
    expect(contentMounted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "我已确认并进入" }));
    await waitFor(() => expect(screen.queryByText("站点内容")).not.toBeNull());
    view.unmount();
    render(<AccessNoticeGate><Content /></AccessNoticeGate>);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("站点内容")).not.toBeNull();
  });

  it("存储不可用时仍需本次主动确认", () => {
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    try {
      render(<AccessNoticeGate><div>站点内容</div></AccessNoticeGate>);
      expect(screen.queryByText("站点内容")).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "我已确认并进入" }));
      expect(screen.queryByText("站点内容")).not.toBeNull();
    } finally { read.mockRestore(); write.mockRestore(); }
  });

  it("首页展示完整声明", () => {
    render(<ComplianceStatement />);
    expect(screen.getByRole("contentinfo").textContent).toContain("本站已在腾讯云提交ICP备案申请");
    expect(screen.getByRole("contentinfo").querySelectorAll("p")).toHaveLength(4);
  });
});
