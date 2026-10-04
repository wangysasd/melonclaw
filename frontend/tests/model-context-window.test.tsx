import { App } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { updateModel } from "../src/api/client";
import { ModelContextWindowEditor } from "../src/components/ModelContextWindow";
import type { ManageableModel } from "../src/types/api";

vi.mock("../src/api/client", () => ({ updateModel: vi.fn() }));
afterEach(cleanup);

const model: ManageableModel = {
  model_key: "model", provider_key: "provider", provider_display_name: "Provider", scope: "user",
  source_type: "manual", display_name: "Model", model_name: "remote", enabled: true,
  is_default: false, input_modalities: ["text"], context_window: 1_000_000, created_by: "user",
};

it("loads the saved model window and submits a changed integer", async () => {
  vi.mocked(updateModel).mockResolvedValue({ ok: true });
  const onChanged = vi.fn();
  render(<App><ModelContextWindowEditor model={model} userId="user" onChanged={onChanged} /></App>);
  fireEvent.click(screen.getByRole("button", { name: "上下文窗口" }));
  const input = await screen.findByRole("spinbutton", { name: "上下文窗口（tokens）" });
  expect((input as HTMLInputElement).value).toBe("1000000");
  fireEvent.change(input, { target: { value: "128000" } });
  fireEvent.blur(input);
  fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
  await waitFor(() => expect(updateModel).toHaveBeenCalledWith("model", { userId: "user", contextWindow: 128000 }));
  expect(onChanged).toHaveBeenCalledOnce();
});

it("does not silently save the default when the field is cleared", async () => {
  render(<App><ModelContextWindowEditor model={model} userId="user" onChanged={() => {}} /></App>);
  fireEvent.click(screen.getByRole("button", { name: "上下文窗口" }));
  const input = await screen.findByRole("spinbutton", { name: "上下文窗口（tokens）" });
  fireEvent.change(input, { target: { value: "" } });
  expect((screen.getByRole("button", { name: /保\s*存/ }) as HTMLButtonElement).disabled).toBe(true);
});
