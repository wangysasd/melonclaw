import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Composer } from "../src/components/Composer";

const session = {
  contextReady: true,
  status: { status: "ready" },
  projects: [{ id: "p" }],
  busy: false,
  runStatus: null,
  conversationCreating: false,
  modelOptions: [],
  selectedModelId: "",
  skills: [
    {
      id: "tushare-fetcher",
      display_name: "Tushare 数据获取",
      description: "获取行情和财务数据",
    },
    {
      id: "report-search",
      display_name: "研报查询",
      description: "查询研究报告内容",
    },
  ],
  skillsLoading: false,
  skillsError: null,
};

vi.mock("../src/state/session", () => ({ useSession: () => session }));

function Harness({ onSend }: { onSend: (value: string, skillId?: string | null) => void }) {
  const [value, setValue] = useState("");
  return (
    <Composer
      value={value}
      onChange={setValue}
      onSend={onSend}
      disabled={false}
    />
  );
}

describe("skill picker", () => {
  it("opens on slash, filters by query, selects with Enter, and can remove the tag", () => {
    const onSend = vi.fn();
    render(<Harness onSend={onSend} />);
    const input = screen.getByRole("textbox");
    expect(input.getAttribute("placeholder")).toBe("聊点儿什么，输入/调用技能工具。");

    fireEvent.change(input, { target: { value: "/" } });
    expect(screen.getByRole("dialog", { name: "可选技能" })).toBeTruthy();
    expect(screen.getByText("Tushare 数据获取")).toBeTruthy();
    expect(screen.getByText("研报查询")).toBeTruthy();
    expect(document.querySelector(".skill-picker-icon")).toBeNull();
    expect(screen.getByRole("option", { name: /Tushare 数据获取/ }).getAttribute("title"))
      .toBe("获取行情和财务数据");

    fireEvent.change(input, { target: { value: "/tush" } });
    expect(screen.getByText("Tushare 数据获取")).toBeTruthy();
    expect(screen.queryByText("研报查询")).toBeNull();
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByLabelText("已选择技能 Tushare 数据获取")).toBeTruthy();
    expect(screen.getByLabelText("已选择技能 Tushare 数据获取").closest(".ant-sender-content")).toBeTruthy();
    expect(input.style.textIndent).toBe("6px");
    expect(input.getAttribute("placeholder")).toBe("");
    expect(document.querySelector<HTMLImageElement>(".skill-logo")?.getAttribute("src"))
      .toBe("/assets/brand/skill-tool.png");
    expect(screen.queryByRole("button", { name: "移除技能 Tushare 数据获取" })).toBeNull();
    fireEvent.keyDown(input, { key: "Backspace" });
    expect(screen.queryByLabelText("已选择技能 Tushare 数据获取")).toBeNull();
  });
});
