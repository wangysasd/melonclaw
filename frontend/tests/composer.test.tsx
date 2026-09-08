import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "../src/components/Composer";
vi.mock("../src/state/session", () => ({ useSession: () => ({ contextReady: true, status: { status: "ready" }, projects: [{ id: "p" }], busy: true, runStatus: "waiting" }) }));

describe("composer", () => {
  it("allows drafting while awaiting approval but cannot send on Enter", () => {
    const onSend = vi.fn(); const onChange = vi.fn();
    render(<Composer value="draft" onChange={onChange} onSend={onSend} disabled />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(input.disabled).toBe(false);
    fireEvent.change(input, { target: { value: "next draft" } });
    expect(onChange).toHaveBeenCalledWith("next draft");
    fireEvent.keyDown(input, { key: "Enter" }); expect(onSend).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "等待确认" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("does not submit an IME confirmation or Shift+Enter", () => {
    const onSend = vi.fn();
    render(<Composer value="你好" onChange={vi.fn()} onSend={onSend} disabled={false} />);
    const input = screen.getByRole("textbox");
    fireEvent.compositionStart(input); fireEvent.keyDown(input, { key: "Enter" }); fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });
});
