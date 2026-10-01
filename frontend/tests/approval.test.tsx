import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalPanel } from "../src/components/ApprovalPanel";
import type { ApprovalAction, PendingApproval } from "../src/types/api";

const action: ApprovalAction = {
  name: "write_file",
  args: JSON.stringify({ file_path: "/notes.txt", content: "hello" }, null, 2),
  description: "Write a file",
  allowed_decisions: ["approve", "edit", "reject"],
};
const approval = (id: string, actions: ApprovalAction[]): PendingApproval => ({
  approval_batch_id: `batch-${id}`,
  assistant_message_id: "assistant-1",
  interrupts: [{ id, actions }],
});

it("shows generated Skill provenance and personal save without enabling", () => {
  render(<ApprovalPanel approval={approval("generated", [{
    name: "confirm_skill_install", description: "Save Skill", allowed_decisions: ["approve", "reject"],
    args: JSON.stringify({ installation: {
      draft_id: "draft", name: "report", scope: "user", enable: false,
      source_url: "", source_ref: "chat:conversation", content_hash: "hash",
    } }),
  }])} onSubmit={vi.fn()} />);
  const manifest = screen.getByLabelText("Skill 安装清单");
  expect(manifest.textContent).toContain("聊天生成");
  expect(manifest.textContent).toContain("仅自己");
  expect(manifest.textContent).toContain("只安装，暂不启用");
});
describe("HITL decisions", () => {
  it("requires an explicit choice for every action and preserves interrupt grouping", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalPanel approval={{ ...approval("a", [action]), interrupts: [{ id: "a", actions: [action] }, { id: "b", actions: [action] }] }} onSubmit={onSubmit} />);
    const submit = screen.getByRole("button", { name: "提交 2 项决定并继续" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    const groups = screen.getAllByRole("group", { name: /处理方式/ });
    await user.click(within(groups[0]).getByRole("button", { name: "允许本次" }));
    expect(submit.disabled).toBe(true);
    await user.click(within(groups[1]).getByRole("button", { name: "拒绝" }));
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith([{ interrupt_id: "a", decisions: [{ type: "approve" }] }, { interrupt_id: "b", decisions: [{ type: "reject", message: "" }] }]);
  });
  it("rejects invalid edits and fixes the tool name when submitting valid JSON", async () => {
    const user = userEvent.setup(); const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalPanel approval={approval("a", [action])} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "编辑参数" }));
    const input = screen.getByLabelText("write_file 的编辑参数");
    await user.clear(input); await user.paste("[]");
    await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
    expect(onSubmit).not.toHaveBeenCalled(); expect(screen.getByRole("alert").textContent).toContain("JSON 对象");
    await user.clear(input); await user.paste('{"content":"updated"}');
    await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
    expect(onSubmit).toHaveBeenCalledWith([{ type: "edit", edited_action: { name: "write_file", args: { content: "updated" } } }]);
  });
  it("requires a nonempty respond result and shows a recoverable submission error", async () => {
    const user = userEvent.setup(); const onSubmit = vi.fn().mockRejectedValue(new Error("同步后重试"));
    render(<ApprovalPanel approval={approval("a", [{ ...action, allowed_decisions: ["respond"] }])} onSubmit={onSubmit} />);
    expect(screen.queryByRole("button", { name: "允许本次" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "提供结果" }));
    await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
    expect(onSubmit).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("write_file 的返回结果"), "手工结果");
    await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
    expect(onSubmit).toHaveBeenCalledWith([{ type: "respond", message: "手工结果" }]);
    expect(screen.getAllByRole("alert").some((el) => el.textContent?.includes("同步后重试"))).toBe(true);
  });
});

it("edits the backend's sanitized JSON string without double serialization", async () => {
  const user = userEvent.setup(); const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ApprovalPanel approval={approval("a", [action])} onSubmit={onSubmit} />);
  await user.click(screen.getByRole("button", { name: "编辑参数" }));
  expect((screen.getByLabelText("write_file 的编辑参数") as HTMLTextAreaElement).value).toBe(action.args);
  await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
  expect(onSubmit).toHaveBeenCalledWith([{ type: "edit", edited_action: { name: "write_file", args: JSON.parse(action.args) } }]);
});

it("shows shared installation effects and submits only an explicit approval", async () => {
  const user = userEvent.setup();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ApprovalPanel approval={approval("skill-approval", [{
    name: "confirm_skill_install", description: "Confirm Skill install", allowed_decisions: ["approve", "reject"],
    args: JSON.stringify({ installation: {
      draft_id: "draft", name: "report", scope: "global", enable: true,
      source_url: "https://github.com/example/report", source_ref: "commit", content_hash: "hash",
    } }),
  }])} onSubmit={onSubmit} />);
  expect(screen.getByLabelText("Skill 安装清单").textContent).toContain("系统共享");
  expect(screen.getByLabelText("Skill 安装清单").textContent).toContain("全员启用");
  expect((screen.getByRole("button", { name: "提交 1 项决定并继续" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "编辑参数" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "允许本次" }));
  await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
  expect(onSubmit).toHaveBeenCalledWith([{ type: "approve" }]);
});
