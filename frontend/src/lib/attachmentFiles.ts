import type { AttachmentCapabilities, AttachmentSummary } from "../types/api";

/** 附件上传前预校验与展示用的纯函数。 */

export function extensionOf(name: string): string {
  const match = /\.([A-Za-z0-9]+)\s*$/.exec(name.trim());
  return match ? `.${match[1].toLowerCase()}` : "";
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 上传前预校验：返回错误文案，通过时返回 null。
 * 只做快速失败，服务端仍会做权威校验。
 */
export function validateAttachmentFile(
  file: File,
  capabilities: AttachmentCapabilities,
  options: { currentCount: number; currentTotalBytes: number },
): string | null {
  const extension = extensionOf(file.name);
  if (!capabilities.items.some((item) => item.extension === extension)) {
    return extension
      ? `暂不支持 ${extension} 类型的附件。`
      : "附件必须有可识别的扩展名。";
  }
  if (file.size <= 0) {
    return `${file.name} 是空文件，无法上传。`;
  }
  if (file.size > capabilities.max_file_bytes) {
    return `${file.name} 超过单个附件上限 ${formatBytes(capabilities.max_file_bytes)}。`;
  }
  if (options.currentCount + 1 > capabilities.max_per_message) {
    return `单条消息最多添加 ${capabilities.max_per_message} 个附件。`;
  }
  if (options.currentTotalBytes + file.size > capabilities.max_total_bytes) {
    return `单条消息附件总大小不能超过 ${formatBytes(capabilities.max_total_bytes)}。`;
  }
  return null;
}

/** 非图片附件的类型角标文案，用于区分 PDF / 表格 / 演示稿等。 */
export function attachmentBadge(name: string): string {
  const label = extensionOf(name).replace(".", "").toUpperCase();
  return label.slice(0, 4) || "FILE";
}

export function attachmentKindOf(extension: string): AttachmentSummary["kind"] {
  if (extension === ".jpg" || extension === ".jpeg" || extension === ".png") {
    return "image";
  }
  if (extension === ".pdf") return "pdf";
  if (
    extension === ".txt" ||
    extension === ".md" ||
    extension === ".csv" ||
    extension === ".json"
  ) {
    return "text";
  }
  return "document";
}
