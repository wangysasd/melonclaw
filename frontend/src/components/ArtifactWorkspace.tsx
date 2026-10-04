import { Dropdown, Segmented } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { conversationArtifacts, fetchResultBlob, resultFileMetadata, resultFileUrl, type ConversationArtifact, type ResultFileMetadata } from "../api/results";
import type { ChatMessage } from "../hooks/useChatStream";
import { assetKey, type AssetRef } from "../lib/resultBlocks";
import { fileLocation, fileRefKey, fileRefPath, type FileLocation, type FileRef } from "../lib/workspaceFiles";
import { downloadBlob } from "../lib/resultExport";
import { formatBytes } from "../lib/attachmentFiles";
import { ArtifactContext, useArtifacts } from "./ArtifactContext";
import { ArtifactPreview } from "./ArtifactPreview";
import { Icon } from "./Icon";
import { ResultAsset } from "./ResultAsset";
import { ResultProvider, type ResultScope } from "./ResultContext";
import { WorkspaceFileBrowser } from "./WorkspaceFileBrowser";

export function ArtifactTrigger() {
  const controls = useArtifacts();
  return controls ? <button type="button" className="tool-catalog-trigger artifact-trigger" onClick={controls.openList} aria-expanded={controls.open} aria-label="查看会话或项目文件">
    <Icon name="panel-right" size={22} /><span className="artifact-trigger-tooltip" aria-hidden="true">文件</span>
  </button> : null;
}

export function MessageArtifactCards({ artifacts }: { artifacts: AssetRef[] }) {
  return artifacts?.length ? <div className="message-artifacts" aria-label="本轮产物">{artifacts.map((asset) => <ResultAsset key={assetKey(asset)} asset={asset} />)}</div> : null;
}

type Selection = { ref: FileRef; messageId?: string; deliveryId?: string };

export function ArtifactWorkspace({ userId, conversationId, projectId, projectName, messages, children, onLocateMessage }: {
  userId: string; conversationId: string | null; projectId: string | null; messages: ChatMessage[]; children: ReactNode;
  projectName?: string;
  onLocateMessage?: (messageId: string, signal: AbortSignal) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [view, setView] = useState<"files" | "preview">("files");
  const [previewMode, setPreviewMode] = useState<"preview" | "source">("preview");
  const [showNavigation, setShowNavigation] = useState(false);
  const [visited, setVisited] = useState(false);
  const [filter, setFilter] = useState<"all" | "delivered">("all");
  const [location, setLocation] = useState<FileLocation>("/");
  const [returnKey, setReturnKey] = useState("");
  const rootLabel = projectId ? `项目文件${projectName ? ` · ${projectName}` : ""}` : "会话文件";
  const [index, setIndex] = useState<ConversationArtifact[]>([]);
  const [indexError, setIndexError] = useState("");
  const [indexLoading, setIndexLoading] = useState(false);
  const [indexRetry, setIndexRetry] = useState(0);
  const [revision, setRevision] = useState(0);
  const [metadata, setMetadata] = useState<ResultFileMetadata | null>(null);
  const [actionError, setActionError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [width, setWidth] = useState(560);
  const [narrow, setNarrow] = useState(() => window.innerWidth <= 1100);
  const panel = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const action = useRef<AbortController | null>(null);
  useEffect(() => {
    const resize = () => setNarrow(window.innerWidth <= 1100);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  useEffect(() => {
    const chat = panel.current?.parentElement?.querySelector(".chat-view");
    if (!chat || !open || !(narrow || expanded)) return;
    chat.setAttribute("inert", "");
    return () => chat.removeAttribute("inert");
  }, [open, narrow, expanded]);
  const scope = useMemo(() => ({ userId, conversationId: conversationId ?? "", projectId, anchorPrefix: "artifact" }), [userId, conversationId, projectId]);
  const terminalKey = messages.filter((message) => message.role === "assistant" && message.status === "completed").map((message) => message.id).join(":");
  const localItems = useMemo(() => messages.filter((message) => message.role === "assistant" && message.status === "completed").reverse().flatMap((message) =>
    (message.artifacts ?? []).map((ref) => ({ ref, message_id: message.id, created_at: message.timestamp ?? "" }))), [messages]);
  const items = useMemo(() => {
    const merged = new Map<string, ConversationArtifact>();
    for (const item of [...localItems, ...index]) if (!merged.has(assetKey(item.ref))) merged.set(assetKey(item.ref), item);
    return [...merged.values()];
  }, [localItems, index]);
  const cache = useMemo(() => ({ scope, terminalKey, indexRetry, controller: new AbortController(), entries: new Map<string, Promise<ResultFileMetadata>>() }), [scope, terminalKey, indexRetry]);
  useEffect(() => () => cache.controller.abort(), [cache]);
  useEffect(() => () => action.current?.abort(), []);
  const getMetadata = useCallback((ref: FileRef) => {
    const key = fileRefKey(ref);
    let pending = cache.entries.get(key);
    if (!pending) {
      pending = resultFileMetadata(ref, scope, cache.controller.signal);
      cache.entries.set(key, pending);
      void pending.catch(() => { if (cache.entries.get(key) === pending) cache.entries.delete(key); });
    }
    return pending;
  }, [cache, scope]);
  useEffect(() => {
    if (!conversationId) return;
    const controller = new AbortController();
    setIndexLoading(true); setIndexError("");
    void conversationArtifacts(conversationId, userId, controller.signal).then((response) => {
      if (!controller.signal.aborted) { setIndex(response.items); setIndexLoading(false); }
    }, (failure: unknown) => {
      if (!controller.signal.aborted) { setIndexLoading(false); setIndexError(failure instanceof Error ? failure.message : "产物列表加载失败。"); }
    });
    return () => controller.abort();
  }, [conversationId, userId, terminalKey, indexRetry]);

  const close = useCallback(() => {
    action.current?.abort(); setOpen(false); setDownloading(false); setActionError(""); setExpanded(false);
    opener.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    if (!open) return;
    panel.current?.focus({ preventScroll: true });
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
      if (event.key === "Tab" && (narrow || expanded)) {
        const controls = [...panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], summary, iframe:not([hidden]), [tabindex="0"]') ?? []]
          .filter((element) => !element.closest("[hidden]") && (!element.closest("details:not([open])") || element.tagName === "SUMMARY"));
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open, close, narrow, expanded]);
  const rememberOpener = () => { if (!open && document.activeElement instanceof HTMLElement) opener.current = document.activeElement; };
  const openArtifact = (ref: FileRef, messageId?: string, sourceConversationId?: string) => {
    if (sourceConversationId && sourceConversationId !== conversationId) return;
    rememberOpener(); action.current?.abort(); setDownloading(false); setActionError("");
    const key = fileRefKey(ref);
    const delivery = items.find((item) => fileRefKey(item.ref) === key);
    const sameFile = selected && fileRefKey(selected.ref) === key;
    if (!sameFile) { setMetadata(null); setRevision(0); setPreviewMode("preview"); }
    if (!open || view === "preview") { setLocation(fileLocation(ref)); setReturnKey(key); }
    setView("preview"); setVisited(true);
    setSelected({ ref, messageId: messageId ?? delivery?.message_id, deliveryId: sameFile ? selected.deliveryId : delivery?.message_id }); setOpen(true);
  };
  const controls = { openArtifact, openList: () => { rememberOpener(); setVisited(true); setOpen(true); }, getMetadata, count: items.length, open };

  const refresh = () => {
    if (!selected) return;
    setActionError(""); setMetadata(null); setRevision((value) => value + 1);
    setSelected({ ...selected, deliveryId: items.find((item) => fileRefKey(item.ref) === fileRefKey(selected.ref))?.message_id });
  };
  const download = async () => {
    if (!selected) return;
    const controller = new AbortController(); action.current?.abort(); action.current = controller;
    setDownloading(true); setActionError("");
    try {
      const info = await resultFileMetadata(selected.ref, scope, controller.signal);
      const blob = await fetchResultBlob(resultFileUrl(selected.ref, scope), controller.signal);
      if (!controller.signal.aborted) downloadBlob(blob, info.file_name);
    } catch (failure) { if (!controller.signal.aborted) setActionError(failure instanceof Error ? failure.message : "下载失败，请重试。"); }
    finally { if (!controller.signal.aborted) setDownloading(false); }
  };
  const locate = async () => {
    if (!selected?.messageId) return;
    const controller = new AbortController(); action.current?.abort(); action.current = controller;
    setActionError("");
    const find = () => document.getElementById(`message-${selected.messageId}`);
    let target = find();
    try {
      if (!target && conversationId) {
        const found = await onLocateMessage?.(selected.messageId, controller.signal);
        if (controller.signal.aborted) return;
        if (!found) { setActionError("找不到这条回复，请重新同步会话。"); return; }
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        target = find();
      }
      if (target) {
        if (narrow || expanded) {
          close();
          await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        }
        if (target.isConnected) { target.scrollIntoView({ behavior: "smooth", block: "center" }); target.focus({ preventScroll: true }); }
      }
    } catch (failure) { if (!controller.signal.aborted) setActionError(failure instanceof Error ? failure.message : "无法定位回复。"); }
  };
  const selectedKey = selected ? fileRefKey(selected.ref) : "";
  const selectedPath = selected ? fileRefPath(selected.ref) : "";
  const delivery = items.find((item) => fileRefKey(item.ref) === selectedKey);
  const updated = Boolean(selected?.deliveryId && delivery && selected.deliveryId !== delivery.message_id);
  const startResize = (event: React.PointerEvent<HTMLDivElement>) => { event.currentTarget.setPointerCapture(event.pointerId); };
  const resize = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      const parent = panel.current?.parentElement?.getBoundingClientRect();
      if (parent) setWidth(Math.max(360, Math.min(parent.width - 360, parent.right - event.clientX)));
    }
  };
  return <ArtifactContext.Provider value={controls}>
    <div className={`chat-artifact-workspace${open ? " artifact-open" : ""}${expanded ? " artifact-expanded" : ""}`} style={{ "--artifact-width": `${width}px` } as CSSProperties}>
      {children}
      {visited ? <aside hidden={!open} className="artifact-drawer" role={narrow || expanded ? "dialog" : undefined} aria-modal={narrow || expanded ? true : undefined} aria-label="文件浏览器" tabIndex={-1} ref={panel}>
        <div className="artifact-resizer" role="separator" aria-label="调整产物面板宽度" aria-orientation="vertical" tabIndex={0} onPointerDown={startResize} onPointerMove={resize} onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)} onKeyDown={(event) => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); setWidth((value) => Math.max(360, Math.min(900, value + (event.key === "ArrowLeft" ? 40 : -40)))); }
        }} />
        <header className="artifact-header">
          <nav className="artifact-document-tabs" aria-label="文件视图">
            <button type="button" aria-pressed={view === "files"} onClick={() => setView("files")}><Icon name="folder" size={16} />文件</button>
            {selected ? <div className={`artifact-document-tab${view === "preview" ? " is-active" : ""}`}>
              <button type="button" aria-pressed={view === "preview"} onClick={() => setView("preview")}><Icon name="file-text" size={16} /><span>{metadata?.file_name ?? selectedPath.split("/").at(-1)}</span></button>
              <button type="button" className="icon-button" aria-label="关闭当前文件" onClick={() => { action.current?.abort(); setDownloading(false); setSelected(null); setMetadata(null); setActionError(""); setView("files"); }}><Icon name="x" size={14} /></button>
            </div> : null}
          </nav>
          <div className="artifact-header-actions">
            {expanded && !narrow && selected && view === "preview" ? <button type="button" className="icon-button" aria-label="切换文件导航" aria-pressed={showNavigation} onClick={() => setShowNavigation((value) => !value)}><Icon name="folder" /></button> : null}
            <button type="button" className="icon-button" aria-label={expanded ? "恢复面板大小" : "放大文件面板"} onClick={() => setExpanded((value) => !value)}><Icon name="scan-search" /></button>
            <button type="button" className="icon-button" aria-label="关闭文件浏览器" onClick={close}><Icon name="x" /></button>
          </div>
        </header>
        {actionError ? <p className="artifact-error" role="alert">{actionError}</p> : null}
        <ResultProvider {...scope}>
          <div className="artifact-content">
            <section className={`artifact-files-pane${view === "preview" ? " is-navigation" : ""}`} hidden={view !== "files" && !(expanded && !narrow && showNavigation)} aria-label="文件列表">
              <WorkspaceFileBrowser scope={scope} location={location} selectedKey={returnKey} currentKey={selectedKey} revision={terminalKey}
                active={open && (view === "files" || (expanded && !narrow && showNavigation))} showFiles={filter === "all"}
                scopeControl={<Segmented aria-label="文件范围" className="file-scope-segmented" value={filter} onChange={(value) => setFilter(value as "all" | "delivered")}
                  options={[{ label: "全部文件", value: "all" }, { label: `本对话产物 · ${items.length}`, value: "delivered" }]} />}
                onOpenFile={(ref) => openArtifact(ref)} />
              {filter === "delivered" ? <div className="artifact-list">
                {indexLoading ? <p role="status">正在加载完整产物列表…</p> : null}
                {indexError ? <div role="alert">{indexError}<button type="button" onClick={() => setIndexRetry((value) => value + 1)}>重试</button></div> : null}
                {!indexLoading && !items.length ? <p className="result-muted">本次对话还没有交付文件。</p> : null}
                {items.map((item) => <ResultProvider key={assetKey(item.ref)} {...scope} messageId={item.message_id}><ResultAsset asset={item.ref} compact /></ResultProvider>)}
              </div> : null}
            </section>
            {selected && open ? <section className="artifact-preview-pane" hidden={view !== "preview"} aria-label="当前文件">
              <div className="artifact-file-toolbar">
                <span className="artifact-path" title={`${rootLabel} / ${selectedPath}`}>{selectedPath}</span>
                {metadata?.preview_kind === "html" || (metadata?.preview_kind === "text" && /\.md$/i.test(metadata.file_name)) ? <Segmented aria-label="展示方式" className="artifact-preview-mode" value={previewMode} onChange={(value) => setPreviewMode(value as "preview" | "source")} options={[{ label: "预览", value: "preview" }, { label: "源码", value: "source" }]} /> : null}
                <button type="button" disabled={downloading} onClick={() => void download()}>下载</button>
                <Dropdown getPopupContainer={(trigger) => trigger.parentElement!} trigger={["click"]} menu={{ items: [
                  { key: "refresh", label: "刷新文件", onClick: refresh },
                  ...(selected.messageId ? [{ key: "locate", label: "定位到回复", onClick: () => void locate() }] : []),
                  { type: "divider" },
                  { key: "info", disabled: true, label: metadata ? `${metadata.media_type} · ${formatBytes(metadata.size_bytes)}` : "读取文件信息…" },
                ] }}><button type="button" aria-label="更多文件操作">⋯</button></Dropdown>
              </div>
              {updated ? <div className="artifact-update" role="status">此文件已重新交付。<button type="button" onClick={refresh}>刷新查看</button></div> : null}
              <ArtifactPreview key={selectedKey} asset={selected.ref} scope={scope as ResultScope} revision={revision} onMetadata={setMetadata} mode={previewMode} />
            </section> : null}
          </div>
        </ResultProvider>
      </aside> : null}
    </div>
  </ArtifactContext.Provider>;
}
