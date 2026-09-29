import { useEffect, useState } from "react";

import { getAttachmentCapabilities } from "../api/client";
import type { AttachmentCapabilities } from "../types/api";

/** 能力接口不可用时使用的最小兜底：与后端首版默认限制保持一致。 */
export const FALLBACK_ATTACHMENT_CAPABILITIES: AttachmentCapabilities = {
  items: [
    { extension: ".jpg", media_type: "image/jpeg", kind: "image" },
    { extension: ".jpeg", media_type: "image/jpeg", kind: "image" },
    { extension: ".png", media_type: "image/png", kind: "image" },
    { extension: ".zip", media_type: "application/zip", kind: "archive" },
    { extension: ".pdf", media_type: "application/pdf", kind: "pdf" },
    { extension: ".txt", media_type: "text/plain", kind: "text" },
    { extension: ".md", media_type: "text/markdown", kind: "text" },
    { extension: ".csv", media_type: "text/csv", kind: "text" },
    { extension: ".json", media_type: "application/json", kind: "text" },
    {
      extension: ".docx",
      media_type:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      kind: "document",
    },
    {
      extension: ".xlsx",
      media_type:
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      kind: "document",
    },
    {
      extension: ".pptx",
      media_type:
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      kind: "document",
    },
  ],
  max_file_bytes: 20 * 1024 * 1024,
  max_total_bytes: 50 * 1024 * 1024,
  max_per_message: 10,
  workspace_max_bytes: 1024 * 1024 * 1024,
  image_max_pixels: 30_000_000,
  pdf_max_pages: 500,
};

/**
 * 读取后端附件能力清单；失败时保留兜底值。
 * 类型与限制以后端为唯一来源，前端只做预校验。
 */
export function useAttachmentCapabilities(): AttachmentCapabilities {
  const [capabilities, setCapabilities] = useState(FALLBACK_ATTACHMENT_CAPABILITIES);
  useEffect(() => {
    const controller = new AbortController();
    getAttachmentCapabilities(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted && Array.isArray(data.items) && data.items.length > 0) {
          setCapabilities(data);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return capabilities;
}
