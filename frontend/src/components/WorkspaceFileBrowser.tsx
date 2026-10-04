import { useEffect, useRef, useState, type ReactNode } from "react";
import { workspaceAttachments, workspaceDirectory } from "../api/results";
import { fileRefKey, workspaceFileRef, type FileLocation, type FileRef } from "../lib/workspaceFiles";
import { Icon } from "./Icon";
import type { ResultScope } from "./ResultContext";

type Row = { name: string; kind: "directory" | "file"; path?: string; ref?: FileRef; size: number | null; modified: string };
type Listing = { rows: Row[]; next: number | null; loading: boolean; error: string };
const EMPTY: Listing = { rows: [], next: null, loading: true, error: "" };

async function readListing(scope: ResultScope, path: FileLocation, offset: number, signal: AbortSignal) {
  const options = { q: "", sort: "name" as const, offset };
  if (path === "attachments") {
    const page = await workspaceAttachments(scope, options, signal);
    return { rows: page.items.map((item): Row => ({ name: item.file_name, kind: "file", ref: { attachment_id: item.attachment_id }, size: item.size_bytes, modified: item.modified_at })), next_offset: page.next_offset };
  }
  const page = await workspaceDirectory(scope, path, options, signal);
  return { rows: page.items.map((item): Row => ({ name: item.name, kind: item.kind, path: item.path, ref: item.kind === "file" ? workspaceFileRef(item.path) : undefined, size: item.size_bytes, modified: item.modified_at })), next_offset: page.next_offset };
}

function WorkspaceFileEntry({ row, scope, currentKey, onOpenFile, active, refreshKey, location }: {
  row: Row; scope: ResultScope; currentKey: string; onOpenFile: (ref: FileRef) => void; active: boolean; refreshKey: string; location: FileLocation;
}) {
  if (row.kind === "directory") return <WorkspaceDirectoryEntry row={row} scope={scope} currentKey={currentKey} onOpenFile={onOpenFile} active={active} refreshKey={refreshKey} location={location} />;
  const key = fileRefKey(row.ref!);
  return <button type="button" className={`workspace-file-row${key === currentKey ? " is-selected" : ""}`}
    data-file-key={key} title={row.path ?? row.name} onClick={() => onOpenFile(row.ref!)}>
    <Icon name="file-text" size={20} /><span className="workspace-file-name">{row.name}</span>
  </button>;
}

function WorkspaceDirectoryEntry({ row, scope, currentKey, onOpenFile, active, refreshKey, location }: {
  row: Row; scope: ResultScope; currentKey: string; onOpenFile: (ref: FileRef) => void; active: boolean; refreshKey: string; location: FileLocation;
}) {
  const path = row.path!;
  const reveal = location === path || location.startsWith(`${path}/`);
  const [expanded, setExpanded] = useState(reveal);
  useEffect(() => { if (reveal) setExpanded(true); }, [reveal, location]);
  const [retry, setRetry] = useState(0);
  const [listing, setListing] = useState<Listing>({ rows: [], next: null, loading: true, error: "" });
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!expanded || !active) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setListing((value) => ({ ...value, loading: true, error: "" }));
    void readListing(scope, path, 0, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      setListing({ rows: page.rows,
        next: page.next_offset, loading: false, error: "" });
    }, (error: unknown) => {
      if (!controller.signal.aborted) setListing((value) => ({ ...value, loading: false, error: error instanceof Error ? error.message : "文件夹加载失败，请重试。" }));
    }).finally(() => { if (request.current === controller) request.current = null; });
    return () => { request.current?.abort(); request.current = null; };
  }, [active, expanded, path, retry, scope, refreshKey]);
  const loadMore = async () => {
    if (listing.next === null || listing.loading) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setListing((value) => ({ ...value, loading: true, error: "" }));
    try {
      const page = await readListing(scope, path, listing.next, controller.signal);
      if (controller.signal.aborted) return;
      const rows = page.rows;
      setListing((value) => {
        const merged = new Map<string, Row>();
        for (const item of [...value.rows, ...rows]) merged.set(item.path ?? fileRefKey(item.ref!), item);
        return { rows: [...merged.values()], next: page.next_offset, loading: false, error: "" };
      });
    } catch (error) {
      if (!controller.signal.aborted) setListing((value) => ({ ...value, loading: false, error: error instanceof Error ? error.message : "文件夹加载失败，请重试。" }));
    } finally { if (request.current === controller) request.current = null; }
  };
  const node = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  useEffect(() => { focused.current = false; }, [active, currentKey]);
  useEffect(() => {
    if (!active || !expanded || listing.loading || !currentKey || focused.current) return;
    const target = [...node.current?.querySelectorAll<HTMLButtonElement>("[data-file-key]") ?? []].find((entry) => entry.dataset.fileKey === currentKey);
    if (target && reveal) { target.focus({ preventScroll: true }); target.scrollIntoView?.({ block: "nearest" }); focused.current = true; }
    else if (reveal && listing.next !== null && !listing.error && !listing.rows.some((entry) => entry.ref ? fileRefKey(entry.ref) === currentKey : location === entry.path || location.startsWith(`${entry.path}/`))) void loadMore();
  });
  return <div className="workspace-file-tree-node" ref={node}>
    <button type="button" className="workspace-file-row workspace-directory-row" aria-expanded={expanded} title={path} onClick={() => setExpanded((value) => !value)}>
      <Icon name={expanded ? "folder-open" : "folder"} size={20} /><span className="workspace-file-name">{row.name}</span><Icon name="chevron-right" size={16} />
    </button>
    {expanded ? <div className="workspace-file-children">
      {listing.loading ? <p className="result-muted" role="status">正在加载文件夹…</p> : null}
      {listing.error ? <div className="workspace-file-branch-error" role="alert">{listing.error}<button type="button" onClick={() => { setListing((value) => ({ ...value, loading: true, error: "" })); setRetry((value) => value + 1); }}>重试</button></div> : null}
      {!listing.loading && !listing.error && !listing.rows.length ? <p className="result-muted">此文件夹为空。</p> : null}
      {listing.rows.map((child) => <WorkspaceFileEntry key={child.path ?? fileRefKey(child.ref!)} row={child} scope={scope} currentKey={currentKey} onOpenFile={onOpenFile} active={active && expanded} refreshKey={refreshKey} location={location} />)}
      {listing.next !== null ? <button type="button" className="file-load-more" disabled={listing.loading} onClick={() => void loadMore()}>加载更多文件</button> : null}
    </div> : null}
  </div>;
}

export function WorkspaceFileBrowser({ scope, location, selectedKey, revision, onOpenFile, active = true, showFiles = true, scopeControl, currentKey = selectedKey }: {
  currentKey?: string; active?: boolean; showFiles?: boolean; scopeControl?: ReactNode;
  scope: ResultScope; location: FileLocation; selectedKey: string; revision: string;
  onOpenFile: (ref: FileRef) => void;
}) {
  const visible = active && showFiles;
  const retained = useRef({ key: "", loaded: 0, ready: false });
  const [refresh, setRefresh] = useState(0);
  const [listing, setListing] = useState<Listing>(EMPTY);
  const more = useRef<(offset: number) => void>(() => undefined);
  const list = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  const { userId, conversationId, projectId } = scope;
  useEffect(() => {
    if (!visible) { setListing((value) => ({ ...value, loading: false })); return; }
    const key = JSON.stringify([userId, conversationId, projectId, location, refresh, revision, selectedKey]);
    const restore = retained.current.key === key && retained.current.ready;
    if (!restore) retained.current = { key, loaded: 0, ready: false };
    let controller: AbortController | null = null;
    let cancelled = false;
    let loaded = retained.current.loaded;
    let foundSelection = false;
    const identity = { userId, conversationId, projectId, anchorPrefix: "workspace-files" };
    const load = async (offset = 0, silent = false) => {
      if (controller) return;
      if (!conversationId) { setListing({ rows: [], next: null, loading: false, error: "请先选择或创建对话。" }); return; }
      controller = new AbortController();
      const current = controller;
      let followOffset: number | null = null;
      if (!silent) setListing((value) => ({ ...value, loading: true, error: "" }));
      try {
        const page = await readListing(identity, "/", offset, current.signal);
        const rows = page.rows;
        const next = page.next_offset;
        if (cancelled) return;
        loaded = offset ? loaded + rows.length : rows.length;
        retained.current = { key, loaded, ready: true };
        setListing((value) => {
          const merged = new Map<string, Row>();
          for (const row of [...(offset ? value.rows : []), ...rows]) merged.set(row.path ?? fileRefKey(row.ref!), row);
          return { rows: [...merged.values()], next, loading: false, error: "" };
        });
        // 返回预览时按需翻到选中的文件；首屏之外也能定位，不扫描其他目录。
        if (!offset) foundSelection = false;
        foundSelection ||= location === "attachments" || rows.some((row) => row.ref ? fileRefKey(row.ref) === selectedKey : location === row.path || location.startsWith(`${row.path}/`));
        if (selectedKey && !foundSelection) followOffset = next;
      } catch (error) {
        if (!cancelled) setListing((value) => ({ ...value, loading: false, error: error instanceof Error ? error.message : "文件列表加载失败，请重试。" }));
      } finally { if (controller === current) controller = null; }
      if (!cancelled && followOffset !== null) void load(followOffset);
    };
    if (!restore) { setListing(EMPTY); focused.current = false; void load(); }
    more.current = (offset) => { void load(offset); };
    // 仅可见的首屏目录轮询；翻页后使用明确刷新，避免打断已展开的大目录。
    const timer = setInterval(() => { if (!document.hidden && loaded <= 200) void load(0, true); }, 5000);
    return () => { cancelled = true; controller?.abort(); clearInterval(timer); };
  }, [userId, conversationId, projectId, location, refresh, revision, selectedKey, visible]);
  useEffect(() => {
    if (!active || !showFiles || focused.current || !selectedKey || listing.loading) return;
    const target = [...list.current?.querySelectorAll<HTMLButtonElement>("[data-file-key]") ?? []].find((row) => row.dataset.fileKey === selectedKey);
    if (target) { target.focus({ preventScroll: true }); target.scrollIntoView?.({ block: "nearest" }); focused.current = true; }
  }, [active, showFiles, selectedKey, listing.loading, listing.rows]);
  return <div className={`workspace-file-browser${showFiles ? "" : " toolbar-only"}`}>
    <div className="file-browser-toolbar">
      {showFiles ? <>
      <button type="button" className="icon-button file-refresh-button" aria-label="刷新文件列表" onClick={() => setRefresh((value) => value + 1)}><Icon name="refresh-cw" /></button>
      </> : null}
      {scopeControl}
    </div>
    <div hidden={!showFiles} className="workspace-file-list" ref={list}>
      <WorkspaceDirectoryEntry row={{ name: "上传附件", kind: "directory", path: "attachments", size: null, modified: "" }} scope={scope} currentKey={currentKey} onOpenFile={onOpenFile} active={visible} refreshKey={`${refresh}:${revision}`} location={location} />
      {listing.error ? <div className="artifact-error" role="alert">{listing.error}<button type="button" onClick={() => setRefresh((value) => value + 1)}>重试</button></div> : null}
      {listing.loading ? <p className="result-muted" role="status">正在加载文件…</p> : null}
      {!listing.loading && !listing.error && !listing.rows.length ? <p className="result-muted">当前目录还没有文件。</p> : null}
      {listing.rows.map((row) => <WorkspaceFileEntry key={row.path ?? fileRefKey(row.ref!)} row={row} scope={scope} currentKey={currentKey}
        onOpenFile={onOpenFile} active={visible} refreshKey={`${refresh}:${revision}`} location={location} />)}
      {listing.next !== null ? <button type="button" className="file-load-more" disabled={listing.loading} onClick={() => more.current(listing.next!)}>加载更多文件</button> : null}
    </div>
  </div>;
}
