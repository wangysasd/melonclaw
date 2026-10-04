import { createContext, useContext, useId, useMemo, type ReactNode } from "react";
import type { AssetRef } from "../lib/resultBlocks";

export type ResultScope = { userId: string; conversationId: string; projectId: string | null; anchorPrefix: string; messageId?: string; fileCardsAtEnd?: boolean; deliveredFiles?: AssetRef[] };
const Context = createContext<ResultScope | null>(null);
export function ResultProvider({ userId, conversationId, projectId, messageId, children }: Omit<ResultScope, "anchorPrefix"> & { children: ReactNode }) {
  const id = useId();
  const value = useMemo(() => ({ userId, conversationId, projectId, messageId, anchorPrefix: `source-${id.replace(/:/g, "")}` }), [userId, conversationId, projectId, messageId, id]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
export function useResultScope() { return useContext(Context); }

/** 每段 Markdown 的引用独立编号，避免执行过程与最终回答的 DOM id 冲突。 */
export function ResultCitationScope({ children, fileCardsAtEnd = false, deliveredFiles }: { children: ReactNode; fileCardsAtEnd?: boolean; deliveredFiles?: AssetRef[] }) {
  const parent = useResultScope();
  const id = useId().replace(/:/g, "");
  const value = useMemo(() => parent ? { ...parent, fileCardsAtEnd, deliveredFiles, anchorPrefix: `source-${id}` } : null, [parent, id, fileCardsAtEnd, deliveredFiles]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
