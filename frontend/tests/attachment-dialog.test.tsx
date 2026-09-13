import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import { AttachmentDialog } from "../src/components/AttachmentDialog";
import * as client from "../src/api/client";
import type { AttachmentCapabilities, AttachmentSummary } from "../src/types/api";

vi.mock("../src/api/client", () => ({
  attachmentContentUrl: (id: string) => `/api/attachments/${id}/content`,
  deleteAttachment: vi.fn(),
  uploadAttachment: vi.fn(),
}));

type UploadedAttachment = AttachmentSummary & { derived_size_bytes?: number };

const capabilities: AttachmentCapabilities = {
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

function makeFile(name: string, size: number, type = "application/octet-stream"): File {
  return new File([new Uint8Array(size)], name, { type });
}

function uploaded(name: string, size: number): UploadedAttachment {
  return {
    attachment_id: `a-${name}`,
    file_name: name,
    media_type: "text/plain",
    kind: "text",
    size_bytes: size,
    parse_status: "pending",
    status: "staged",
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

function renderDialog(
  overrides: Partial<ComponentProps<typeof AttachmentDialog>> = {},
) {
  const onConfirm = vi.fn();
  const onClose = vi.fn();
  const view = render(
    <AttachmentDialog
      open
      sessionId={1}
      initialFiles={[]}
      capabilities={capabilities}
      userId="u1"
      tenantId="t1"
      projectId="p1"
      existingCount={0}
      existingTotalBytes={0}
      onClose={onClose}
      onConfirm={onConfirm}
      {...overrides}
    />,
  );
  return { view, onConfirm, onClose };
}

beforeEach(() => {
  vi.mocked(client.uploadAttachment).mockReset();
  vi.mocked(client.uploadAttachment).mockImplementation(async (_projectId, input) =>
    uploaded(input.file.name, input.file.size),
  );
  vi.mocked(client.deleteAttachment).mockReset();
  vi.mocked(client.deleteAttachment).mockResolvedValue({} as never);
});

describe("attachment dialog", () => {
  it("states the upload requirements from capabilities", () => {
    renderDialog();
    expect(screen.getByText(/支持 PNG \/ TXT/)).toBeTruthy();
    expect(screen.getByText(/单个文件不超过 10 B/)).toBeTruthy();
    expect(screen.getByText(/单条消息最多 2 个、合计不超过 25 B/)).toBeTruthy();
  });

  it("uploads selected files and lists them", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain"), makeFile("b.png", 4, "image/png")] },
    });
    expect(await screen.findByText("a.txt")).toBeTruthy();
    expect(screen.getByText("b.png")).toBeTruthy();
    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("已上传")).toHaveLength(2);
  });

  it("keeps confirm disabled until something is uploaded", async () => {
    renderDialog();
    const ok = screen.getByRole("button", { name: "确认添加附件" }) as HTMLButtonElement;
    expect(ok.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain")] },
    });
    await flush();
    expect((screen.getByRole("button", { name: "确认添加附件" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("rejects unsupported, oversized, and over-quota files", async () => {
    renderDialog();
    const input = screen.getByLabelText("选择附件");

    fireEvent.change(input, { target: { files: [makeFile("tool.exe", 1)] } });
    expect(screen.getByRole("alert").textContent).toContain("暂不支持");

    fireEvent.change(input, { target: { files: [makeFile("big.txt", 11, "text/plain")] } });
    expect(screen.getByRole("alert").textContent).toContain("单个附件上限");

    fireEvent.change(input, {
      target: {
        files: [
          makeFile("a.txt", 1, "text/plain"),
          makeFile("b.txt", 1, "text/plain"),
          makeFile("c.txt", 1, "text/plain"),
        ],
      },
    });
    expect(screen.getByRole("alert").textContent).toContain("最多添加 2 个附件");

    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(2);
  });

  it("removes an item and deletes the staged attachment", async () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain")] },
    });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "移除 a.txt" }));
    expect(screen.queryByText("a.txt")).toBeNull();
    expect(client.deleteAttachment).toHaveBeenCalledWith("a-a.txt", {
      userId: "u1",
      tenantId: "t1",
    });
  });

  it("hands confirmed items to the caller without deleting them", async () => {
    const { onConfirm, onClose } = renderDialog();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain")] },
    });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "确认添加附件" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0][0].file_name).toBe("a.txt");
    expect(client.deleteAttachment).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("purges staged uploads when cancelled", async () => {
    const { onConfirm, onClose } = renderDialog();
    fireEvent.change(screen.getByLabelText("选择附件"), {
      target: { files: [makeFile("a.txt", 5, "text/plain")] },
    });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(client.deleteAttachment).toHaveBeenCalledWith("a-a.txt", {
      userId: "u1",
      tenantId: "t1",
    });
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("uploads files passed in from the composer", async () => {
    renderDialog({ initialFiles: [makeFile("dropped.txt", 3, "text/plain")] });
    expect(await screen.findByText("dropped.txt")).toBeTruthy();
    await flush();
    expect(client.uploadAttachment).toHaveBeenCalledTimes(1);
  });
});
