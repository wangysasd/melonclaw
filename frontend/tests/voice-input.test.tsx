import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "../src/components/Composer";
import * as client from "../src/api/client";
import { knowledgeApi } from "../src/api/knowledge";
import { transcribeSpeech } from "../src/api/speech";
import { startRecording } from "../src/lib/speechRecorder";

vi.mock("../src/api/client", () => ({
  attachmentContentUrl: (id: string) => `/api/attachments/${id}/content`,
  deleteAttachment: vi.fn(),
  getAttachment: vi.fn(),
  retryAttachmentParse: vi.fn(),
  uploadAttachment: vi.fn(),
  getAttachmentCapabilities: vi.fn(),
}));
vi.mock("../src/api/knowledge", () => ({
  knowledgeApi: { list: vi.fn().mockResolvedValue({ items: [], total: 0 }) },
}));
vi.mock("../src/api/speech", () => ({ transcribeSpeech: vi.fn() }));
vi.mock("../src/lib/speechRecorder", () => ({ startRecording: vi.fn() }));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() }, notification: {}, modal: {} }),
    },
  };
});

const session = {
  contextReady: true,
  status: { status: "ready" },
  userId: "u1",
  epoch: 1,
  projectId: "p1",
  conversationId: "c1" as string | null,
  draftConversationId: null as string | null,
  ensureConversation: vi.fn(),
  busy: false,
  conversationCreating: false,
  modelOptions: [{
    id: "custom:deepseek-flash",
    display_name: "DeepSeek Flash",
    source: "system",
    provider: "deepseek",
    provider_key: "deepseek",
    model: "deepseek-v4-flash",
    available: true,
    is_default: true,
    input_modalities: ["text"],
  }],
  selectedModelId: "custom:deepseek-flash",
  selectModel: vi.fn(),
  skills: [],
  skillsLoading: false,
  skillsError: null,
};
vi.mock("../src/state/session", () => ({ useSession: () => session }));

const start = vi.mocked(startRecording);
const transcribe = vi.mocked(transcribeSpeech);

function renderComposer(props: Partial<ComponentProps<typeof Composer>> = {}) {
  const onChange = vi.fn();
  const onSend = vi.fn();
  const view = render(
    <Composer value="你好世界" onChange={onChange} onSend={onSend} disabled={false} pendingInteraction={null} {...props} />,
  );
  return { ...view, onChange, onSend };
}

beforeEach(() => {
  session.userId = "u1";
  session.conversationId = "c1";
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
  vi.mocked(client.getAttachmentCapabilities).mockReset();
  vi.mocked(client.getAttachmentCapabilities).mockResolvedValue({ items: [] } as never);
  vi.mocked(knowledgeApi.list).mockReset();
  vi.mocked(knowledgeApi.list).mockResolvedValue({ items: [], total: 0 });
  start.mockReset();
  transcribe.mockReset();
});

describe("voice input", () => {
  it("inserts the transcript at the cursor and does not send", async () => {
    start.mockResolvedValue({
      stop: vi.fn().mockResolvedValue(new Blob(["wav"])),
      cancel: vi.fn(),
      analyser: null as unknown as AnalyserNode,
    });
    transcribe.mockResolvedValue({ text: " 天气 ", duration_ms: 8 });
    const { onChange, onSend } = renderComposer();
    const input = screen.getByRole("textbox", { name: "输入内容" }) as HTMLTextAreaElement;
    input.focus();
    input.setSelectionRange(2, 2);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
    });
    expect(screen.getByText("录音中 0s")).toBeTruthy();
    expect((screen.getByRole("button", { name: "发送" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "结束录音并转写" }));
    });
    await waitFor(() => expect(onChange).toHaveBeenCalledWith("你好天气世界"));
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each(["conversation", "hidden"])("drops the transcript when the chat scope changes: %s", async (change) => {
    let resolveTranscript: (value: { text: string; duration_ms: number }) => void = () => undefined;
    start.mockResolvedValue({
      stop: vi.fn().mockResolvedValue(new Blob(["wav"])),
      cancel: vi.fn(),
      analyser: null as unknown as AnalyserNode,
    });
    transcribe.mockImplementation(() => new Promise((resolve) => { resolveTranscript = resolve; }));
    const { onChange, rerender } = renderComposer();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "结束录音并转写" }));
    });
    expect(screen.getByRole("button", { name: "正在转写，点击取消" })).toBeTruthy();
    if (change === "conversation") session.conversationId = "c2";
    rerender(<Composer active={change !== "hidden"} value="你好世界" onChange={onChange} onSend={vi.fn()} disabled={false} pendingInteraction={null} />);
    expect(transcribe.mock.calls[0][1].aborted).toBe(true);
    await act(async () => {
      resolveTranscript({ text: "天气", duration_ms: 1 });
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("releases the microphone on leaving chat and ignores a late recording limit", async () => {
    const cancel = vi.fn();
    const stop = vi.fn();
    let onLimit = () => undefined;
    start.mockImplementation(async (limit) => {
      onLimit = limit;
      return { stop, cancel, analyser: null as unknown as AnalyserNode };
    });
    const { onChange, onSend, rerender } = renderComposer();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
    });
    rerender(<Composer active={false} value="你好世界" onChange={onChange} onSend={onSend} disabled={false} pendingInteraction={null} />);
    await act(async () => { onLimit(); });
    expect(cancel).toHaveBeenCalledOnce();
    expect(stop).not.toHaveBeenCalled();
    expect(transcribe).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "语音输入" })).toBeTruthy();
  });

  it("submits the recording once at the time limit and leaves an empty transcript out of the draft", async () => {
    let onLimit = () => undefined;
    const stop = vi.fn().mockResolvedValue(new Blob(["wav"]));
    start.mockImplementation(async (limit) => {
      onLimit = limit;
      return { stop, cancel: vi.fn(), analyser: null as unknown as AnalyserNode };
    });
    transcribe.mockResolvedValue({ text: "  ", duration_ms: 120000 });
    const { onChange, onSend } = renderComposer();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
    });
    await act(async () => { onLimit(); });
    expect(stop).toHaveBeenCalledOnce();
    expect(transcribe).toHaveBeenCalledOnce();
    expect(onChange).not.toHaveBeenCalled();
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "语音输入" })).toBeTruthy();
  });

  it("releases a microphone granted after the user cancels the permission wait", async () => {
    let grant = () => undefined;
    const cancel = vi.fn();
    const stop = vi.fn();
    start.mockImplementation(() => new Promise((resolve) => {
      grant = () => resolve({ stop, cancel, analyser: null as unknown as AnalyserNode });
    }));
    renderComposer();
    const voice = screen.getByRole("button", { name: "语音输入" });
    await act(async () => { fireEvent.click(voice); });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "结束录音并转写" }));
      grant();
    });
    expect(cancel).toHaveBeenCalledOnce();
    expect(stop).not.toHaveBeenCalled();
    expect(transcribe).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "语音输入" })).toBeTruthy();
  });

  it("disables the microphone outside a secure context", () => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: false });
    renderComposer();
    const button = screen.getByRole("button", { name: "非 HTTPS 页面无法使用语音输入" });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(button);
    expect(start).not.toHaveBeenCalled();
  });
});
