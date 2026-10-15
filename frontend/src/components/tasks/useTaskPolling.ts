import { useEffect, useState } from "react";

/** 单个请求在途；页面隐藏时取消，切换对象或用户时旧响应不再写状态。 */
export function useTaskPolling<T>(load: (signal: AbortSignal) => Promise<T>, interval: number) {
  const [snapshot, setSnapshot] = useState<{ load: typeof load; data?: T; error: string }>();
  useEffect(() => {
    let disposed = false;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      if (disposed || document.hidden) return;
      controller?.abort();
      const current = new AbortController();
      controller = current;
      try {
        const value = await load(current.signal);
        if (!disposed && !current.signal.aborted) setSnapshot({ load, data: value, error: "" });
      } catch (err) {
        if (!disposed && !current.signal.aborted) setSnapshot({ load, error: err instanceof Error ? err.message : "读取任务失败" });
      } finally {
        if (!disposed && !current.signal.aborted) timer = setTimeout(() => void poll(), interval);
      }
    };
    const visibility = () => { clearTimeout(timer); controller?.abort(); if (!document.hidden) void poll(); };
    document.addEventListener("visibilitychange", visibility);
    void poll();
    return () => { disposed = true; controller?.abort(); clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [load, interval]);
  // 筛选条件或身份变化时立即撤下旧数据，不能等待新请求完成后才切换。
  return snapshot?.load === load ? { data: snapshot.data, error: snapshot.error } : { data: undefined, error: "" };
}
