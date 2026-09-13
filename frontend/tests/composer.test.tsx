import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import { Composer } from "../src/components/Composer";
import * as client from "../src/api/client";
import type { AttachmentCapabilities, AttachmentSummary } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  attachmentContentUrl: (id: string) => `/api/attachments/${id}/content`,
  deleteAttachment: vi.fn(),
  getAttachment: vi.fn(),
  retryAttachmentParse: vi.fn(),
  uploadAttachment: vi.fn(),
  getAttachmentCapabilities: vi.fn(),
}));

// jsdom 里没有 antd <App> provider，useApp() 的 message 无法调用；这里替换成可断言的桩。
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({
        message: {
          error: vi.fn(),
          warning: vi.fn(),
          success: vi.fn(),
          info: vi.fn(),
        },
        notification: {},
        modal: {},
      }),
    },
  };
});

const session = {
  contextReady: true,
  status: { status: "ready" },
  projects: [{ id: "p" }],
  userId: "u1",
  tenantId: "t1",
  projectId: "p1",
  conversationId: "c1",
  busy: false,
  runStatus: null as string | null,
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

type UploadedAttachment = AttachmentSummary & { derived_size_bytes?: number };

const capabilitiesPayload: AttachmentCapabilities = {
  items: [
    { extension: ".png", media_type: "image/png", kind: "image" },
    { extension: ".txt", media_type: "text/plain", kind: "text" },
  ],
  max_file_bytes: 10,
  max_total_bytes: 25,
  max_per_message: 2,
  project_max_bytes: 100,
  image_max_pixels: 100,
  pdf_max_pages: 1,
};

function uploadedAttachment(overrides: Partial<UploadedAttachment>): UploadedAttachment {
  return {
    attachment_id: "a1",
    file_name: "doc.txt",
    media_type: "text/plain",
    kind: "text",
    size_bytes: 5,
    parse_status: "processed",
    status: "staged",
    ...overrides,
  };
}

function makeFile(name: string, size: number, type = "application/octet-stream"): File {
  return new File([new Uint8Array(size)], name, { type });
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

async function renderComposer(
  props: Partial<ComponentProps<typeof Composer>> = {},
) {
  const view = render(
    <Composer
      value=""
      onChange={vi.fn()}
      onSend={vi.fn()}
      disabled={false}
      {...props}
    />,
  );
  // 等待 capabilities 请求落地，让预校验使用 mock 返回的限制。
  await flush();
  return view;
}

function composerElement(view: ReturnType<typeof render>): HTMLElement {
  return view.container.querySelector(".composer") as HTMLElement;
}

beforeEach(() => {
  session.busy = false;
  session.runStatus = null;
  session.conversationCreating = false;
  session.projectId = "p1";
  session.userId = "u1";
  session.tenantId = "t1";

  vi.mocked(client.getAttachmentCapabilities).mockReset();
  vi.mocked(client.getAttachmentCapabilities).mockResolvedValue(capabilitiesPayload);
  vi.mocked(client.uploadAttachment).mockReset();
  vi.mocked(client.uploadAttachment).mockImplementation(async (_projectId, input) =>
    uploadedAttachment({
      attachment_id: `a-${input.file.name}`,
      file_name: input.file.name,
      kind: input.file.name.endsWith(".png") ? "image" : "text",
      media_type: input.file.type || "text/plain",
      size_bytes: input.file.size,
    }),
  );
  vi.mocked(client.retryAttachmentParse).mockReset();
  vi.mocked(client.retryAttachmentParse).mockResolvedValue(uploadedAttachment({}));
  vi.mocked(client.getAttachment).mockReset();
  vi.mocked(client.getAttachment).mockRejectedValue(new Error("not found"));
  vi.mocked(client.deleteAttachment).mockReset();
  vi.mocked(client.deleteAttachment).mockResolvedValue(uploadedAttachment({}));
});

describe("composer", () => {
  it("allows drafting while awaiting approval but cannot send on Enter", async () => {
    session.busy = true;
    session.runStatus = "waiting";
    const onSend = vi.fn(); const onChange = vi.fn();
    const view = await renderComposer({ value: "draft", onChange, onSend, disabled: true });
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
    view.unmount();
  });

  it("does not submit an IME confirmation or Shift+Enter", async () => {
    const onSend = vi.fn();
    const view = await renderComposer({ value: "你好", onSend, disabled: false });
    const input = screen.getByRole("textbox");
    fireEvent.compositionStart(input); fireEvent.keyDown(input, { key: "Enter" }); fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    view.unmount();
  });

  it("submits a non-empty draft once on Enter", async () => {
    const onSend = vi.fn();
    const view = await renderComposer({ value: "你好", onSend, disabled: false });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith("你好");
    view.unmount();
  });

  it("submits after an initially empty draft is edited", async () => {
    const onSend = vi.fn();
    const view = await renderComposer({ value: "", onSend, disabled: false });
    view.rerender(<Composer value="后来输入" onChange={vi.fn()} onSend={onSend} disabled={false} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("后来输入");
    view.unmount();
  });

  it("keeps whitespace-only drafts unsendable", async () => {
    const onSend = vi.fn();
    const view = await renderComposer({ value: "   \n", onSend, disabled: false });
    const sendButton = screen.getByRole("button", { name: "发送" });
    expect((sendButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    view.unmount();
  });

  it("shows model choices to the left of send and reports a selection", async () => {
    const view = await renderComposer({ value: "你好", disabled: false });
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
    view.unmount();
  });

  it("uploads every file from a multi-select", async () => {
    const view = await renderComposer();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.png", 1, "image/png"), makeFile("b.txt", 1, "text/plain")] },
    });
    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(screen.getByText("a.png")).toBeTruthy();
    expect(screen.getByText("b.txt")).toBeTruthy();
    view.unmount();
  });

  it("rejects unsupported files before uploading", async () => {
    const view = await renderComposer();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("tool.exe", 1)] },
    });
    await flush();
    expect(client.uploadAttachment).not.toHaveBeenCalled();
    expect(screen.getByText(/暂不支持/)).toBeTruthy();
    view.unmount();
  });

  it("rejects files above the per-file size limit", async () => {
    const view = await renderComposer();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("big.txt", 11, "text/plain")] },
    });
    await flush();
    expect(client.uploadAttachment).not.toHaveBeenCalled();
    expect(screen.getByText(/单个附件上限/)).toBeTruthy();
    view.unmount();
  });

  it("caps the number of attachments per message", async () => {
    const view = await renderComposer();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: {
        files: [
          makeFile("a.txt", 1, "text/plain"),
          makeFile("b.txt", 1, "text/plain"),
          makeFile("c.txt", 1, "text/plain"),
        ],
      },
    });
    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/最多添加 2 个附件/)).toBeTruthy();
    view.unmount();
  });

  it("adds attachments pasted from the clipboard", async () => {
    const view = await renderComposer();
    fireEvent.paste(composerElement(view), {
      clipboardData: { files: [makeFile("pasted.png", 1, "image/png")] },
    });
    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(1);
    expect(vi.mocked(client.uploadAttachment).mock.calls[0][1].file.name).toBe("pasted.png");
    view.unmount();
  });

  it("adds attachments dropped onto the composer", async () => {
    const view = await renderComposer();
    fireEvent.drop(composerElement(view), {
      dataTransfer: { files: [makeFile("dropped.txt", 1, "text/plain")] },
    });
    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(1);
    expect(vi.mocked(client.uploadAttachment).mock.calls[0][1].file.name).toBe("dropped.txt");
    view.unmount();
  });

  it("offers a re-parse action after a parse failure", async () => {
    vi.mocked(client.uploadAttachment).mockResolvedValue(
      uploadedAttachment({ attachment_id: "a1", file_name: "doc.txt", parse_status: "failed" }),
    );
    const view = await renderComposer();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("doc.txt", 5, "text/plain")] },
    });
    fireEvent.click(await screen.findByRole("button", { name: "重新解析" }));
    await flush();
    expect(client.retryAttachmentParse).toHaveBeenCalledWith("a1", {
      userId: "u1",
      tenantId: "t1",
    });
    view.unmount();
  });

  it("opens an image preview from the attachment chip", async () => {
    const view = await renderComposer();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("shot.png", 1, "image/png")] },
    });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "预览 shot.png" }));
    expect(screen.getByRole("dialog", { name: "shot.png" })).toBeTruthy();
    view.unmount();
  });
});
