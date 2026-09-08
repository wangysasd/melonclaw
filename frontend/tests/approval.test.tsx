import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApprovalPanel } from "../src/components/ApprovalPanel";

const action = { name: "write_file", args: { file_path: "/notes.txt", content: "hello" } };
describe("HITL decisions", () => {
  it("requires an explicit choice for every action and preserves interrupt grouping", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ApprovalPanel approval={{ interrupts: [{ id: "a", actions: [action] }, { id: "b", actions: [action] }] }} onSubmit={onSubmit} />);
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
    render(<ApprovalPanel approval={{ id: "a", actions: [action] }} onSubmit={onSubmit} />);
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
    render(<ApprovalPanel approval={{ actions: [{ ...action, allowed_decisions: ["respond"] }] }} onSubmit={onSubmit} />);
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
  render(<ApprovalPanel approval={{ id: "a", actions: [{ ...action, args: JSON.stringify(action.args, null, 2) }] }} onSubmit={onSubmit} />);
  await user.click(screen.getByRole("button", { name: "编辑参数" }));
  expect((screen.getByLabelText("write_file 的编辑参数") as HTMLTextAreaElement).value).toBe(JSON.stringify(action.args, null, 2));
  await user.click(screen.getByRole("button", { name: "提交 1 项决定并继续" }));
  expect(onSubmit).toHaveBeenCalledWith([{ type: "edit", edited_action: { name: "write_file", args: action.args } }]);
});
