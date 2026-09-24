import { App as AntdApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ProjectDialog } from "../src/components/ProjectDialog";
import type { SessionContextValue } from "../src/state/session";

const session = {
  createProject: vi.fn().mockResolvedValue({ id: "p-new", name: "新项目" }),
  moveConversationToProject: vi.fn().mockResolvedValue(true),
};

vi.mock("../src/state/session", () => ({ useSession: () => session as unknown as SessionContextValue }));

it("creates a project and moves the selected ordinary conversation into it", async () => {
  const onClose = vi.fn();
  session.createProject.mockResolvedValueOnce({ id: "p-new", name: "新项目" });
  render(
    <AntdApp>
      <ProjectDialog open moveConversationId="c-ordinary" onClose={onClose} />
    </AntdApp>,
  );

  fireEvent.change(screen.getByRole("textbox", { name: "项目名称" }), { target: { value: "新项目" } });
  fireEvent.click(screen.getByRole("button", { name: "创建项目" }));

  await waitFor(() => expect(session.createProject).toHaveBeenCalledWith("新项目", { openAfterCreate: false }));
  await waitFor(() => expect(session.moveConversationToProject).toHaveBeenCalledWith("c-ordinary", "p-new"));
  expect(onClose).toHaveBeenCalledOnce();
});
