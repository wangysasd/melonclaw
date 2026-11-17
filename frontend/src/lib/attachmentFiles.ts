import type { AttachmentCapabilities, AttachmentTypeSpec } from "../types/api";

/** 附件上传前预校验与展示用的纯函数。 */

export function extensionOf(name: string): string {
  const match = /\.([A-Za-z0-9]+)\s*$/.exec(name.trim());
  return match ? `.${match[1].toLowerCase()}` : "";
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  const megabytes = value / (1024 * 1024);
  return `${Number.isInteger(megabytes) ? megabytes : megabytes.toFixed(1)} MB`;
}

export function usesAttachmentOcr(spec: AttachmentTypeSpec | undefined, modelSupportsImage?: boolean): boolean {
  return spec?.ocr === "always" || (spec?.ocr === "text_model" && modelSupportsImage === false);
}

/**
 * 上传前预校验：返回错误文案，通过时返回 null。
 * 只做快速失败，服务端仍会做权威校验。
 */
export function validateAttachmentFile(
  file: File,
  capabilities: AttachmentCapabilities,
  options: { currentCount: number; currentTotalBytes: number; modelSupportsImage?: boolean },
): string | null {
  const extension = extensionOf(file.name);
  if (!capabilities.items.length) return "附件格式和限制暂未加载，请稍后重试。";
  const spec = capabilities.items.find((item) => item.extension === extension);
  if (!spec) {
    return extension
      ? `暂不支持 ${extension} 类型的附件。`
      : "附件必须有可识别的扩展名。";
  }
  if (file.size <= 0) {
    return `${file.name} 是空文件，无法上传。`;
  }
  const maxFileBytes = usesAttachmentOcr(spec, options.modelSupportsImage)
    ? capabilities.ocr_max_file_bytes : capabilities.max_file_bytes;
  if (file.size > maxFileBytes) {
    return `${file.name} 超过单个附件上限 ${formatBytes(maxFileBytes)}。`;
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

/** 上传要求文案：类型、单文件大小、单条消息数量与总大小限制。 */
export function describeAttachmentLimits(
  capabilities: AttachmentCapabilities,
): string {
  if (!capabilities.items.length) return "正在读取附件格式和限制…";
  const extensions = capabilities.items
    .map((item) => item.extension.replace(".", "").toUpperCase())
    .join(" / ");
  return [
    `支持 ${extensions}`,
    `单个文件不超过 ${formatBytes(capabilities.max_file_bytes)}` +
      (capabilities.ocr_max_file_bytes < capabilities.max_file_bytes ? `，OCR 附件不超过 ${formatBytes(capabilities.ocr_max_file_bytes)}` : ""),
    `PDF 最多 ${capabilities.pdf_max_pages} 页`,
    `单条消息最多 ${capabilities.max_per_message} 个、合计不超过 ${formatBytes(capabilities.max_total_bytes)}`,
  ].join("；") + "。";
}
