import { describe, expect, it } from "vitest";

import {
  attachmentBadge,
  attachmentKindOf,
  describeAttachmentLimits,
  extensionOf,
  formatBytes,
  validateAttachmentFile,
} from "../src/lib/attachmentFiles";
import type { AttachmentCapabilities } from "../src/types/api";

const capabilities: AttachmentCapabilities = {
  items: [
    { extension: ".png", media_type: "image/png", kind: "image" },
    { extension: ".txt", media_type: "text/plain", kind: "text" },
  ],
  max_file_bytes: 100,
  max_total_bytes: 150,
  max_per_message: 2,
  workspace_max_bytes: 1000,
  image_max_pixels: 100,
  pdf_max_pages: 1,
};

function file(name: string, size: number, type = "application/octet-stream"): File {
  return new File([new Uint8Array(size)], name, { type });
}

describe("attachment file helpers", () => {
  it("normalizes extensions case-insensitively", () => {
    expect(extensionOf("Report.PNG")).toBe(".png");
    expect(extensionOf("archive.tar.gz")).toBe(".gz");
    expect(extensionOf("no-extension")).toBe("");
  });

  it("formats byte sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3 MB");
    expect(formatBytes(1.5 * 1024 * 1024)).toBe("1.5 MB");
  });

  it("describes upload requirements from capabilities", () => {
    const text = describeAttachmentLimits({
      ...capabilities,
      max_file_bytes: 10 * 1024 * 1024,
      max_total_bytes: 25 * 1024 * 1024,
    });
    expect(text).toContain("支持 PNG / TXT");
    expect(text).toContain("单个文件不超过 10 MB");
    expect(text).toContain("单条消息最多 2 个、合计不超过 25 MB");
  });

  it("maps extensions to attachment kinds", () => {
    expect(attachmentKindOf(".png")).toBe("image");
    expect(attachmentKindOf(".pdf")).toBe("pdf");
    expect(attachmentKindOf(".csv")).toBe("text");
    expect(attachmentKindOf(".docx")).toBe("document");
  });

  it("derives a short type badge", () => {
    expect(attachmentBadge("季度报表.xlsx")).toBe("XLSX");
    expect(attachmentBadge("slides.pptx")).toBe("PPTX");
    expect(attachmentBadge("no-extension")).toBe("FILE");
  });

  it("rejects unsupported, empty, oversized, and over-quota files", () => {
    expect(
      validateAttachmentFile(file("a.exe", 10), capabilities, {
        currentCount: 0,
        currentTotalBytes: 0,
      }),
    ).toContain("暂不支持");

    expect(
      validateAttachmentFile(file("empty.txt", 0), capabilities, {
        currentCount: 0,
        currentTotalBytes: 0,
      }),
    ).toContain("空文件");

    expect(
      validateAttachmentFile(file("big.txt", 101), capabilities, {
        currentCount: 0,
        currentTotalBytes: 0,
      }),
    ).toContain("单个附件上限");

    expect(
      validateAttachmentFile(file("c.txt", 10), capabilities, {
        currentCount: 2,
        currentTotalBytes: 10,
      }),
    ).toContain("最多添加 2 个附件");

    expect(
      validateAttachmentFile(file("d.txt", 60), capabilities, {
        currentCount: 1,
        currentTotalBytes: 100,
      }),
    ).toContain("总大小");
  });

  it("accepts files inside every limit", () => {
    expect(
      validateAttachmentFile(file("ok.txt", 50), capabilities, {
        currentCount: 1,
        currentTotalBytes: 50,
      }),
    ).toBeNull();
  });
});
