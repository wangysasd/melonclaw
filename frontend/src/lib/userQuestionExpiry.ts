import { useEffect, useState } from "react";

const MAX_TIMEOUT_MS = 2_147_000_000;

export function isUserQuestionExpired(expiresAt: string | undefined): boolean {
  if (!expiresAt) return false;
  const timestamp = Date.parse(expiresAt);
  return !Number.isNaN(timestamp) && timestamp <= Date.now();
}

/** 在 TTL 跨过当前时间时主动重渲染，不依赖父组件碰巧更新。 */
export function useUserQuestionExpired(expiresAt: string | undefined): boolean {
  const [expired, setExpired] = useState(() => isUserQuestionExpired(expiresAt));

  useEffect(() => {
    let timeoutId: number | undefined;

    const update = () => {
      const timestamp = expiresAt ? Date.parse(expiresAt) : Number.NaN;
      if (Number.isNaN(timestamp)) {
        setExpired(false);
        return;
      }
      const remaining = timestamp - Date.now();
      if (remaining <= 0) {
        setExpired(true);
        return;
      }
      setExpired(false);
      timeoutId = window.setTimeout(update, Math.min(remaining + 20, MAX_TIMEOUT_MS));
    };

    update();
    return () => {
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [expiresAt]);

  return expired;
}
