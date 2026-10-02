import { createContext, useContext, useId, useMemo, type ReactNode } from "react";

export type ResultScope = { userId: string; conversationId: string; projectId: string | null; anchorPrefix: string };
const Context = createContext<ResultScope | null>(null);
export function ResultProvider({ userId, conversationId, projectId, children }: Omit<ResultScope, "anchorPrefix"> & { children: ReactNode }) {
  const id = useId();
  const value = useMemo(() => ({ userId, conversationId, projectId, anchorPrefix: `source-${id.replace(/:/g, "")}` }), [userId, conversationId, projectId, id]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useResultScope() { return useContext(Context); }

/** 每段 Markdown 的引用独立编号，避免执行过程与最终回答的 DOM id 冲突。 */
export function ResultCitationScope({ children }: { children: ReactNode }) {
  const parent = useResultScope();
  const id = useId().replace(/:/g, "");
  const value = useMemo(() => parent ? { ...parent, anchorPrefix: `source-${id}` } : null, [parent, id]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
