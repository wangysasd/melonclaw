import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectPicker } from "../src/components/ProjectPicker";
import type { SessionContextValue } from "../src/state/session";

const session = {
  contextReady: true,
  projectId: "p1",
  projects: [
    { id: "p1", name: "第一个项目" },
    { id: "p2", name: "资料整理" },
  ],
  openProject: vi.fn().mockResolvedValue(undefined),
  closeProject: vi.fn(),
};

vi.mock("../src/state/session", () => ({ useSession: () => session as unknown as SessionContextValue }));

beforeEach(() => {
  vi.clearAllMocks();
  session.projectId = "p1";
});

describe("composer project picker", () => {
  it("reflects a sidebar project change and does not toggle the current project off", () => {
    const view = render(<ProjectPicker />);
    expect(screen.getByRole("button", { name: "当前项目：第一个项目，选择项目" })).toBeTruthy();
    session.projectId = "p2";
    view.rerender(<ProjectPicker />);
    fireEvent.click(screen.getByRole("button", { name: "当前项目：资料整理，选择项目" }));
    fireEvent.click(screen.getByRole("option", { name: "资料整理" }));
    expect(session.openProject).not.toHaveBeenCalled();
  });

  it("searches and switches to another project", () => {
    render(<ProjectPicker />);
    fireEvent.click(screen.getByRole("button", { name: "当前项目：第一个项目，选择项目" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索项目" }), { target: { value: "资料" } });
    expect(screen.queryByRole("option", { name: "第一个项目" })).toBeNull();
    fireEvent.click(screen.getByRole("option", { name: "资料整理" }));
    expect(session.openProject).toHaveBeenCalledWith("p2");
  });

  it("leaves the selected project from the leading icon while keeping the picker available", () => {
    const view = render(<ProjectPicker />);
    fireEvent.click(screen.getByRole("button", { name: "退出项目「第一个项目」" }));
    expect(session.closeProject).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "当前项目：第一个项目，选择项目" }));
    expect(screen.getByRole("listbox", { name: "项目列表" })).toBeTruthy();

    session.projectId = "";
    view.rerender(<ProjectPicker />);
    expect(screen.queryByRole("button", { name: "退出项目「第一个项目」" })).toBeNull();
    expect(screen.getByRole("button", { name: "选择项目" })).toBeTruthy();
  });

  it("supports leaving projects and opening the existing new-project dialog", () => {
    const onOpenProjectDialog = vi.fn();
    render(<ProjectPicker onOpenProjectDialog={onOpenProjectDialog} />);
    fireEvent.click(screen.getByRole("button", { name: "当前项目：第一个项目，选择项目" }));
    fireEvent.click(screen.getByRole("button", { name: "不在项目中工作" }));
    expect(session.closeProject).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "当前项目：第一个项目，选择项目" }));
    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    expect(onOpenProjectDialog).toHaveBeenCalledOnce();
  });
});
