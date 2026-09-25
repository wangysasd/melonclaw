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
  projects: [{ id: "p", name: "项目" }],
  userId: "u1",
  projectId: "p1",
  conversationId: "c1",
  draftConversationId: null as string | null,
  ensureConversation: vi.fn(),
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
  skills: [
    { id: "deep-research", display_name: "深度研究", description: "研究并总结一个主题" },
    { id: "image-gen", display_name: "生成图片", description: "根据描述生成图片" },
  ],
  skillsLoading: false,
  skillsError: null,
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
  workspace_max_bytes: 100,
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

async function flush(times = 3): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
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

/** 打开左下角加号的二级目录。 */
function openPlusMenu(): void {
  fireEvent.click(screen.getByRole("button", { name: "添加附件" }));
}

/** 经二级目录的「图片和文件」打开添加附件弹窗。 */
function openFilesDialog(): void {
  openPlusMenu();
  fireEvent.click(screen.getByRole("menuitem", { name: "图片和文件" }));
}

/** 走完整弹窗流程：打开 → 选择文件 → 等上传完成 → 确认添加。 */
async function addViaDialog(files: File[]): Promise<void> {
  openFilesDialog();
  fireEvent.change(screen.getByLabelText("选择附件"), { target: { files } });
  await flush();
  expect(screen.getAllByText("已上传")).toHaveLength(files.length);
  fireEvent.click(screen.getByRole("button", { name: "确认" }));
  await flush();
}

beforeEach(() => {
  session.busy = false;
  session.runStatus = null;
  session.conversationCreating = false;
  session.projectId = "p1";
  session.conversationId = "c1";
  session.draftConversationId = null;
  session.ensureConversation.mockReset();
  session.userId = "u1";

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

  it("shows a stop square while running and only stops on click", async () => {
    session.busy = true;
    session.runStatus = "processing";
    const onSend = vi.fn(); const onStop = vi.fn(); const onChange = vi.fn();
    const view = await renderComposer({ value: "draft", onChange, onSend, disabled: false, isRunning: true, onStop });
    const stopButton = screen.getByRole("button", { name: "停止生成" });
    expect((stopButton as HTMLButtonElement).disabled).toBe(false);
    // 回车不触发停止也不发送。
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    expect(onStop).not.toHaveBeenCalled();
    fireEvent.click(stopButton);
    expect(onStop).toHaveBeenCalledTimes(1);
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

  it("opens a two-level menu from the plus button", async () => {
    const view = await renderComposer();
    openPlusMenu();
    expect(screen.getByRole("menu", { name: "添加内容" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "图片和文件" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "技能" })).toBeTruthy();
    view.unmount();
  });

  it("reveals the skill list on hover and applies the chosen skill", async () => {
    const view = await renderComposer();
    openPlusMenu();
    expect(screen.queryByRole("listbox", { name: "技能列表" })).toBeNull();

    fireEvent.pointerOver(screen.getByRole("menuitem", { name: "技能" }));
    expect(screen.getByRole("listbox", { name: "技能列表" })).toBeTruthy();
    fireEvent.click(screen.getByRole("option", { name: /深度研究/ }));

    expect(screen.queryByRole("menu", { name: "添加内容" })).toBeNull();
    expect(screen.getByText("深度研究")).toBeTruthy();
    view.unmount();
  });

  it("keeps the skill list open when the pointer leaves the skills row", async () => {
    const view = await renderComposer();
    openPlusMenu();
    const skillsItem = screen.getByRole("menuitem", { name: "技能" });
    fireEvent.pointerOver(skillsItem);
    expect(screen.getByRole("listbox", { name: "技能列表" })).toBeTruthy();

    // 鼠标从「技能」移向飞出的二级目录时，会先离开这一行；此时目录必须留着。
    fireEvent.pointerOut(skillsItem, { relatedTarget: document.body });
    expect(screen.getByRole("listbox", { name: "技能列表" })).toBeTruthy();

    // 直接落在二级目录的某一项上也要保持展开。
    fireEvent.pointerOver(screen.getByRole("option", { name: /生成图片/ }));
    expect(screen.getByRole("listbox", { name: "技能列表" })).toBeTruthy();
    view.unmount();
  });

  it("closes the skill list only after another top-level entry is hovered", async () => {
    const view = await renderComposer();
    openPlusMenu();
    fireEvent.pointerOver(screen.getByRole("menuitem", { name: "技能" }));
    expect(screen.getByRole("listbox", { name: "技能列表" })).toBeTruthy();

    fireEvent.pointerOver(screen.getByRole("menuitem", { name: "图片和文件" }));
    expect(screen.queryByRole("listbox", { name: "技能列表" })).toBeNull();
    view.unmount();
  });

  it("closes the plus menu when clicking outside or pressing Escape", async () => {
    const view = await renderComposer();
    openPlusMenu();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu", { name: "添加内容" })).toBeNull();

    openPlusMenu();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu", { name: "添加内容" })).toBeNull();
    view.unmount();
  });

  it("opens the attachment dialog from the plus menu", async () => {
    const view = await renderComposer();
    openFilesDialog();
    expect(screen.getByText("点击选择文件，或把文件拖到这里")).toBeTruthy();
    expect(screen.getByText(/支持 PNG \/ TXT/)).toBeTruthy();
    expect(screen.queryByRole("menu", { name: "添加内容" })).toBeNull();
    view.unmount();
  });

  it("keeps the attachment dialog open when a blank recent chat gets its storage ID", async () => {
    session.projectId = "";
    session.conversationId = null;
    session.ensureConversation.mockImplementation(async () => {
      session.conversationId = "c2";
      session.draftConversationId = "c2";
      return { id: "c2", project_id: null };
    });
    const view = await renderComposer();
    openFilesDialog();
    await flush();
    expect(session.ensureConversation).toHaveBeenCalledTimes(1);
    expect(screen.getByText("点击选择文件，或把文件拖到这里")).toBeTruthy();
    view.unmount();
  });

  it("opens project attachments without creating an empty conversation", async () => {
    session.conversationId = null;
    const view = await renderComposer();
    openFilesDialog();
    await flush();
    expect(session.ensureConversation).not.toHaveBeenCalled();
    expect(screen.getByText("点击选择文件，或把文件拖到这里")).toBeTruthy();
    view.unmount();
  });

  it("adds attachments only after the dialog is confirmed", async () => {
    const view = await renderComposer();
    openFilesDialog();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain"), makeFile("b.png", 4, "image/png")] },
    });
    await flush();
    expect(screen.queryByText("a.txt")).toBeTruthy();
    // 确认前输入区不应出现附件卡片。
    expect(view.container.querySelector(".composer-attachments")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await flush();
    const chips = view.container.querySelector(".composer-attachments") as HTMLElement;
    expect(chips).not.toBeNull();
    expect(chips.textContent).toContain("a.txt");
    expect(chips.textContent).toContain("b.png");
    view.unmount();
  });

  it("opens the dialog with files dropped onto the composer", async () => {
    const view = await renderComposer();
    fireEvent.drop(composerElement(view), {
      dataTransfer: { files: [makeFile("dropped.txt", 1, "text/plain")] },
    });
    await flush();
    expect(vi.mocked(client.uploadAttachment).mock.calls[0][1].file.name).toBe("dropped.txt");
    expect(screen.getAllByText("已上传")).toHaveLength(1);
    view.unmount();
  });

  it("opens the dialog with pasted images", async () => {
    const view = await renderComposer();
    fireEvent.paste(composerElement(view), {
      clipboardData: { files: [makeFile("pasted.png", 1, "image/png")] },
    });
    await flush();
    expect(vi.mocked(client.uploadAttachment).mock.calls[0][1].file.name).toBe("pasted.png");
    view.unmount();
  });

  it("drops staged uploads when the dialog is cancelled", async () => {
    const view = await renderComposer();
    openFilesDialog();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain")] },
    });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await flush();
    expect(client.deleteAttachment).toHaveBeenCalledWith("a-a.txt", {
      userId: "u1",
      projectId: "p1",
    });
    expect(view.container.querySelector(".composer-attachments")).toBeNull();
    view.unmount();
  });

  it("offers a re-parse action after a parse failure", async () => {
    vi.mocked(client.uploadAttachment).mockResolvedValue(
      uploadedAttachment({ attachment_id: "a1", file_name: "doc.txt", parse_status: "failed" }),
    );
    const view = await renderComposer();
    await addViaDialog([makeFile("doc.txt", 5, "text/plain")]);
    fireEvent.click(await screen.findByRole("button", { name: "重新解析" }));
    await flush();
    expect(client.retryAttachmentParse).toHaveBeenCalledWith("a1", {
      userId: "u1",
      projectId: "p1",
      conversationId: null,
    });
    view.unmount();
  });

  it("opens an image preview from the attachment chip", async () => {
    vi.mocked(client.uploadAttachment).mockResolvedValue(
      uploadedAttachment({
        attachment_id: "img1",
        file_name: "shot.png",
        kind: "image",
        media_type: "image/png",
        parse_status: "not_required",
      }),
    );
    const view = await renderComposer();
    await addViaDialog([makeFile("shot.png", 1, "image/png")]);
    fireEvent.click(screen.getByRole("button", { name: "预览 shot.png" }));
    expect(screen.getByRole("dialog", { name: "shot.png" })).toBeTruthy();
    view.unmount();
  });
});
