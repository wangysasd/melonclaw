import { Alert, Button, Drawer, Spin, Tag } from "antd";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { readKnowledgeDocumentText, type KnowledgeDocumentText } from "../api/knowledge";
import { ApiError } from "../api/client";
import { knowledgeReferenceKey, parseKnowledgeTextReference, type KnowledgeTextReference } from "../lib/knowledgeCitation";
import { useSession } from "../state/session";

interface CitationTarget {
  reference: KnowledgeTextReference | null;
  error?: string;
  key: number;
}

interface CitationContextValue {
  openFromHref: (href: string, allowed?: ReadonlySet<string> | null) => boolean;
}

const SourceAllowContext = createContext<ReadonlySet<string> | null>(null);

export function KnowledgeSourceAllow({ allow, children }: { allow: ReadonlySet<string> | null; children: ReactNode }) {
  return <SourceAllowContext.Provider value={allow}>{children}</SourceAllowContext.Provider>;
}

export function useKnowledgeSourceAllow() {
  return useContext(SourceAllowContext);
}

const Context = createContext<CitationContextValue | null>(null);

export function useKnowledgeCitation() {
  return useContext(Context);
}

function requestError(error: unknown): string {
  if (!(error instanceof ApiError)) return "原文暂时无法加载，请稍后重试。";
  if (error.status === 404) return "原引用版本不可用（可能已过期或权限已变更），未替换为当前版本。";
  if (error.status === 403) return "当前账户无权查看此原文，或访问权限已变更。";
  if (error.status === 401) return "登录状态已失效，请重新登录后打开原文。";
  return error.message || "原文暂时无法加载，请稍后重试。";
}

export function KnowledgeCitationProvider({ children }: { children: ReactNode }) {
  const session = useSession();
  const [target, setTarget] = useState<CitationTarget | null>(null);
  const nextKey = useRef(0);
  const openFromHref = useCallback((href: string, allowed?: ReadonlySet<string> | null) => {
    const origin = window.location.origin;
    const text = parseKnowledgeTextReference(href, origin);
    const referenceKey = knowledgeReferenceKey(href, origin);
    if (text.kind === "not-knowledge") return false;
    const key = ++nextKey.current;
    if (text.kind === "invalid" || !referenceKey) {
      setTarget({ reference: null, error: "原文引用链接无效，未打开链接。", key });
      return true;
    }
    if (allowed && !allowed.has(referenceKey)) {
      setTarget({ reference: null, error: "此链接不在本次检索结果中，未打开。", key });
      return true;
    }
    setTarget({ reference: text.kind === "reference" ? text.value : null, key });
    return true;
  }, []);

  useEffect(() => {
    setTarget(null);
  }, [session.epoch, session.userId, session.conversationId]);

  return <Context.Provider value={{ openFromHref }}>
    {children}
    <KnowledgeTextDrawer key={target?.key ?? "closed"} target={target} onClose={() => setTarget(null)} />
  </Context.Provider>;
}

function KnowledgeTextDrawer({ target, onClose }: { target: CitationTarget | null; onClose: () => void }) {
  const [page, setPage] = useState<KnowledgeDocumentText | null>(null);
  const [content, setContent] = useState("");
  const [offset, setOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const open = Boolean(target);

  useEffect(() => {
    if (!target?.reference) {
      setPage(null);
      setContent("");
      setOffset(null);
      setError(target?.error ?? "");
      return;
    }
    const reference = target.reference;
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    setError("");
    setPage(null);
    setContent("");
    setOffset(null);
    void readKnowledgeDocumentText(reference.kbId, reference.documentId, {
      revisionId: reference.revisionId,
      offset: reference.offset,
      limit: reference.limit,
    }, abort.signal).then((response) => {
      if (abort.signal.aborted) return;
      setPage(response);
      setContent(response.content);
      setOffset(response.next_offset);
    }).catch((reason: unknown) => {
      if (!abort.signal.aborted) setError(requestError(reason));
    }).finally(() => {
      if (!abort.signal.aborted) setLoading(false);
    });
    return () => {
      abort.abort();
      controller.current?.abort();
      if (controller.current === abort) controller.current = null;
    };
  }, [target]);

  const loadNext = async () => {
    if (!target?.reference || offset === null || loading) return;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    setError("");
    try {
      const response = await readKnowledgeDocumentText(target.reference.kbId, target.reference.documentId, {
        revisionId: target.reference.revisionId,
        offset,
        limit: target.reference.limit,
      }, abort.signal);
      if (abort.signal.aborted) return;
      setPage(response);
      setContent((current) => current + response.content);
      setOffset(response.next_offset);
    } catch (reason) {
      if (!abort.signal.aborted) setError(requestError(reason));
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  };

  return <Drawer
    title={page?.title ?? "知识库原文"}
    open={open}
    onClose={onClose}
    width={560}
    className="knowledge-source-drawer"
    destroyOnHidden
    extra={page ? <Tag color={page.is_current ? "blue" : "default"}>{page.is_current ? "当前版本" : `历史版本 · 第 ${page.revision_no} 版`}</Tag> : null}
  >
    {page ? <div className="knowledge-source-meta">全文共 {page.total_chars} 字符</div> : null}
    {error ? <Alert type="error" showIcon message={error} /> : null}
    {loading && !page ? <div className="knowledge-source-loading"><Spin /></div> : null}
    {content ? <pre className="knowledge-source-text">{content}</pre> : null}
    {page && offset !== null ? <Button className="knowledge-source-more" onClick={() => void loadNext()} loading={loading}>继续查看原文</Button> : null}
    {loading && page ? <div className="knowledge-source-loading"><Spin size="small" /></div> : null}
  </Drawer>;
}

