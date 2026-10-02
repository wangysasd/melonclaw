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

it("submits directly only after a click and shows a file summary with collapsed raw parameters", async () => {
  const user = userEvent.setup(); const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ApprovalPanel approval={approval("direct", [action])} onSubmit={onSubmit} />);
  expect(screen.getByText("/notes.txt")).toBeTruthy();
  expect(screen.getByText("查看完整参数 · write_file").closest("details")?.open).toBe(false);
  expect(onSubmit).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "允许本次" }));
  expect(onSubmit).toHaveBeenCalledTimes(1);
  expect(onSubmit).toHaveBeenCalledWith([{ type: "approve" }]);
});

it("rejects directly with optional feedback and never offers autonomous authorization", async () => {
  const user = userEvent.setup(); const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<ApprovalPanel approval={approval("reject", [action])} onSubmit={onSubmit} />);
  expect(screen.queryByRole("button", { name: /AI 自己决定/ })).toBeNull();
  await user.click(screen.getByText("补充拒绝原因（可选）"));
  await user.type(screen.getByLabelText("write_file 的拒绝原因"), "请保留原文件");
  await user.click(screen.getByRole("button", { name: "拒绝" }));
  expect(onSubmit).toHaveBeenCalledWith([{ type: "reject", message: "请保留原文件" }]);
});

it("keeps unknown operations readable and recovers from failed direct submissions", async () => {
  const user = userEvent.setup(); const onSubmit = vi.fn().mockRejectedValueOnce(new Error("未接收，请重试")).mockResolvedValue(undefined);
  render(<ApprovalPanel approval={approval("unknown", [{ ...action, name: "external_tool" }])} onSubmit={onSubmit} />);
  expect(screen.getByText("查看完整参数 · external_tool").closest("details")?.open).toBe(true);
  await user.click(screen.getByRole("button", { name: "允许本次" }));
  expect(screen.getByRole("alert").textContent).toContain("未接收");
  await user.click(screen.getByRole("button", { name: "拒绝" }));
  expect(onSubmit).toHaveBeenLastCalledWith([{ type: "reject", message: "" }]);
});

it("shows the full command and edit previews without executing them", () => {
  render(<ApprovalPanel approval={approval("details", [
    { ...action, name: "execute", args: JSON.stringify({ command: "echo hello\necho world", cwd: "/workspace" }) },
    { ...action, name: "edit_file", args: JSON.stringify({ file_path: "/notes.txt", old_string: "before", new_string: "after", replace_all: true }) },
  ])} onSubmit={vi.fn()} />);
  expect(screen.getByText("echo hello echo world").textContent).toContain("echo world");
  expect(screen.getByText("工作目录：/workspace")).toBeTruthy();
  expect(screen.getByText("− before").className).toBe("diff-removed");
  expect(screen.getByText("+ after").className).toBe("diff-added");
  expect(screen.getByText("替换全部匹配项")).toBeTruthy();
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
  expect(screen.queryByRole("button", { name: "提交 1 项决定并继续" })).toBeNull();
  expect(screen.queryByRole("button", { name: "编辑参数" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "允许本次" }));
  expect(onSubmit).toHaveBeenCalledWith([{ type: "approve" }]);
});
