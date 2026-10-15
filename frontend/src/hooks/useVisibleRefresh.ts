import { useEffect } from "react";

/** 静默刷新后台交付；串行请求，隐藏/卸载时撤销，恢复可见时立即同步。 */
export function useVisibleRefresh(refresh: (signal: AbortSignal) => Promise<void>, interval: number, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (disposed || document.hidden) return;
      const current = new AbortController();
      controller = current;
      try { await refresh(current.signal); } catch { /* 保留上次成功状态，下一轮重试。 */ }
      finally { if (!disposed && !current.signal.aborted) timer = setTimeout(() => void poll(), interval); }
    };
    const visibility = () => {
      clearTimeout(timer); controller?.abort();
      if (!document.hidden) void poll();
    };
    document.addEventListener("visibilitychange", visibility);
    if (!document.hidden) timer = setTimeout(() => void poll(), interval);
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", visibility); };
  }, [refresh, interval, enabled]);
}
