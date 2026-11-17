import { useEffect, useState } from "react";

import { getAttachmentCapabilities } from "../api/client";
import type { AttachmentCapabilities } from "../types/api";

const LOADING_CAPABILITIES: AttachmentCapabilities = {
  items: [],
  max_file_bytes: 0,
  max_total_bytes: 0,
  max_per_message: 0,
  workspace_max_bytes: 0,
  image_max_pixels: 0,
  pdf_max_pages: 0,
  ocr_max_file_bytes: 0,
  ocr_max_pages: 0,
};

/** 类型与限额由能力接口提供。 */
export function useAttachmentCapabilities(ready: boolean, dialogSessionId: number): AttachmentCapabilities {
  const [capabilities, setCapabilities] = useState(LOADING_CAPABILITIES);
  const loaded = capabilities.items.length > 0;
  useEffect(() => {
    if (!ready || loaded) return;
    const controller = new AbortController();
    getAttachmentCapabilities(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted && Array.isArray(data.items) && data.items.length > 0) {
          setCapabilities(data);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [ready, loaded, dialogSessionId]);
  return capabilities;
}
