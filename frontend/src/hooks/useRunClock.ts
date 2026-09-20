import { useEffect, useState } from "react";

/** 250ms 足够让 0.1s 精度看起来连续，又不会把时间线拖进高频重渲染。 */
const TICK_MS = 250;

/**
 * 运行级计时：只在 `active` 且拿得到开始时间时开启定时器。
 *
 * 挂在执行区域状态头部，所以一次 tick 只重渲染头部，
 * 不会带着步骤列表和 Markdown 一起重渲染。终态、卸载、切换会话由 effect 清理。
 */
export function useElapsedMs(
  startedAt: number | null,
  active: boolean,
): number | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active || startedAt === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [active, startedAt]);

  if (startedAt === null || !active) return null;
  return Math.max(0, now - startedAt);
}
