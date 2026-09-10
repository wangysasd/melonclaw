import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "../src/components/Composer";

const session = {
  contextReady: true,
  status: { status: "ready" },
  projects: [{ id: "p" }],
  busy: false,
  runStatus: null,
  conversationCreating: false,
  modelOptions: [
    {
      id: "system:deepseek:flash",
      display_name: "DeepSeek Flash",
      source: "system",
      provider: "deepseek",
      model: "deepseek-v4-flash",
      available: true,
      is_default: true,
    },
    {
      id: "system:deepseek:pro",
      display_name: "DeepSeek Pro",
      source: "system",
      provider: "deepseek",
      model: "deepseek-v4-pro",
      available: true,
      is_default: false,
    },
  ],
  selectedModelId: "system:deepseek:flash",
  selectModel: vi.fn(),
};
vi.mock("../src/state/session", () => ({ useSession: () => session }));

describe("composer", () => {
  it("allows drafting while awaiting approval but cannot send on Enter", () => {
    session.busy = true;
    session.runStatus = "waiting";
    const onSend = vi.fn(); const onChange = vi.fn();
    render(<Composer value="draft" onChange={onChange} onSend={onSend} disabled />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
    const picker = screen.getByRole("combobox", { name: "选择模型" }) as HTMLSelectElement;
    expect(picker.disabled).toBe(false);
    fireEvent.mouseDown(picker);
    fireEvent.click(screen.getByText("deepseek-v4-pro"));
    expect(session.selectModel).toHaveBeenCalledWith("system:deepseek:pro");
    fireEvent.change(input, { target: { value: "next draft" } });
    expect(onChange).toHaveBeenCalledWith("next draft");
    fireEvent.keyDown(input, { key: "Enter" }); expect(onSend).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "等待确认" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("does not submit an IME confirmation or Shift+Enter", () => {
    session.busy = false;
    session.runStatus = null;
    const onSend = vi.fn();
    render(<Composer value="你好" onChange={vi.fn()} onSend={onSend} disabled={false} />);
    const input = screen.getByRole("textbox");
    fireEvent.compositionStart(input); fireEvent.keyDown(input, { key: "Enter" }); fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("submits a non-empty draft once on Enter", () => {
    session.busy = false;
    session.runStatus = null;
    const onSend = vi.fn();
    render(<Composer value="你好" onChange={vi.fn()} onSend={onSend} disabled={false} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("你好");
  });

  it("submits after an initially empty draft is edited", () => {
    const onSend = vi.fn();
    const view = render(<Composer value="" onChange={vi.fn()} onSend={onSend} disabled={false} />);
    view.rerender(<Composer value="后来输入" onChange={vi.fn()} onSend={onSend} disabled={false} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("后来输入");
  });

  it("keeps whitespace-only drafts unsendable", () => {
    const onSend = vi.fn();
    render(<Composer value={"   \n"} onChange={vi.fn()} onSend={onSend} disabled={false} />);
    const sendButton = screen.getByRole("button", { name: "发送" });
    expect((sendButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows model choices to the left of send and reports a selection", () => {
    render(<Composer value="你好" onChange={vi.fn()} onSend={vi.fn()} disabled={false} />);
    const picker = screen.getByRole("combobox", { name: "选择模型" }) as HTMLSelectElement;
    const sendButton = screen.getByRole("button", { name: "发送" });
    expect(screen.getByText("deepseek-v4-flash")).toBeTruthy();
    expect(sendButton.textContent).toBe("");
    expect(
      picker.compareDocumentPosition(sendButton) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    fireEvent.mouseDown(picker);
    fireEvent.click(screen.getByText("deepseek-v4-pro"));
    expect(session.selectModel).toHaveBeenCalledWith("system:deepseek:pro");
  });
});
