import { useEffect, useRef, useState, type ReactNode } from "react";
import { useWorkspaceFileListing } from "../hooks/useWorkspaceFileListing";
import { EMPTY_LISTING, fileRowKey, mergeFileRows, readFileListing, ROOT_SOURCES, SOURCE_LABELS, type FileRow as Row, type FileListing as Listing } from "../lib/workspaceFileListing";
import { fileRefKey, type FileLocation, type FileRef } from "../lib/workspaceFiles";
import { Icon } from "./Icon";
import type { ResultScope } from "./ResultContext";

function FileSource({ source }: { source: Row["source"] }) {
  return source ? <span className={`workspace-file-source${source === "上传" ? " is-upload" : " is-output"}`}>{source}</span> : null;
}

function WorkspaceFileEntry({ row, scope, currentKey, onOpenFile, active, refreshKey, location }: {
  row: Row; scope: ResultScope; currentKey: string; onOpenFile: (ref: FileRef) => void; active: boolean; refreshKey: string; location: FileLocation;
}) {
  if (row.kind === "directory") return <WorkspaceDirectoryEntry row={row} scope={scope} currentKey={currentKey} onOpenFile={onOpenFile} active={active} refreshKey={refreshKey} location={location} />;
  const key = fileRefKey(row.ref!);
  return <button type="button" className={`workspace-file-row${key === currentKey ? " is-selected" : ""}`}
    data-file-key={key} title={row.path ?? row.name} onClick={() => onOpenFile(row.ref!)}>
    <Icon name="file-text" size={20} /><span className="workspace-file-name">{row.name}</span><FileSource source={row.source} />
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
  const [listing, setListing] = useState<Listing>(EMPTY_LISTING);
  const request = useRef<AbortController | null>(null);
  const retained = useRef("");
  useEffect(() => {
    if (!expanded || !active) return;
    const cancel = () => { request.current?.abort(); request.current = null; setListing((value) => ({ ...value, loading: false })); };
    const key = JSON.stringify([path, scope.userId, scope.conversationId, scope.projectId, refreshKey, retry]);
    if (retained.current === key) return cancel;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setListing((value) => ({ ...value, loading: true, error: "" }));
    void readFileListing(scope, path, 0, controller.signal).then((page) => {
      if (controller.signal.aborted) return;
      retained.current = key;
      setListing({ rows: page.rows,
        next: page.next_offset, loading: false, error: "" });
    }, (error: unknown) => {
      if (!controller.signal.aborted) setListing((value) => ({ ...value, loading: false, error: error instanceof Error ? error.message : "文件夹加载失败，请重试。" }));
    }).finally(() => { if (request.current === controller) request.current = null; });
    return cancel;
  }, [active, expanded, path, retry, scope, refreshKey]);
  const loadMore = async () => {
    if (listing.next === null || listing.loading) return;
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setListing((value) => ({ ...value, loading: true, error: "" }));
    try {
      const page = await readFileListing(scope, path, listing.next, controller.signal);
      if (controller.signal.aborted) return;
      setListing((value) => ({ rows: mergeFileRows(value.rows, page.rows), next: page.next_offset, loading: false, error: "" }));
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
      <Icon name={expanded ? "folder-open" : "folder"} size={20} /><span className="workspace-file-name">{row.name}</span><FileSource source={row.source} /><Icon name="chevron-right" size={16} />
    </button>
    {expanded ? <div className="workspace-file-children">
      {listing.loading ? <p className="result-muted" role="status">正在加载文件夹…</p> : null}
      {listing.error ? <div className="workspace-file-branch-error" role="alert">{listing.error}<button type="button" onClick={() => { setListing((value) => ({ ...value, loading: true, error: "" })); setRetry((value) => value + 1); }}>重试</button></div> : null}
      {!listing.loading && !listing.error && !listing.rows.length ? <p className="result-muted">此文件夹为空。</p> : null}
      {listing.rows.map((child) => <WorkspaceFileEntry key={fileRowKey(child)} row={child} scope={scope} currentKey={currentKey} onOpenFile={onOpenFile} active={active && expanded} refreshKey={refreshKey} location={location} />)}
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
  const [refresh, setRefresh] = useState(0);
  const { rows, listings, loading, more, retry } = useWorkspaceFileListing(scope, visible, `${refresh}:${revision}`, location, selectedKey);
  const list = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  useEffect(() => { focused.current = false; }, [selectedKey, visible]);
  useEffect(() => {
    if (!active || !showFiles || focused.current || !selectedKey || loading) return;
    const target = [...list.current?.querySelectorAll<HTMLButtonElement>("[data-file-key]") ?? []].find((row) => row.dataset.fileKey === selectedKey);
    if (target) { target.focus({ preventScroll: true }); target.scrollIntoView?.({ block: "nearest" }); focused.current = true; }
  }, [active, showFiles, selectedKey, loading, rows]);
  return <div className={`workspace-file-browser${showFiles ? "" : " toolbar-only"}`}>
    <div className="file-browser-toolbar">
      {showFiles ? <>
      <button type="button" className="icon-button file-refresh-button" aria-label="刷新文件列表" onClick={() => setRefresh((value) => value + 1)}><Icon name="refresh-cw" /></button>
      </> : null}
      {scopeControl}
    </div>
    <div hidden={!showFiles} className="workspace-file-list" ref={list}>
      {ROOT_SOURCES.map((source) => listings[source].error ? <div className="workspace-file-branch-error" role="alert" key={source}>
        <span>{SOURCE_LABELS[source]}加载失败：{listings[source].error}</span>
        <button type="button" onClick={() => retry(source)}>重试</button>
      </div> : null)}
      {loading ? <p className="result-muted" role="status">正在加载文件…</p> : null}
      {!loading && !ROOT_SOURCES.some((source) => listings[source].error) && !rows.length ? <p className="result-muted">当前还没有文件。</p> : null}
      {rows.map((row) => <WorkspaceFileEntry key={fileRowKey(row)} row={row} scope={scope} currentKey={currentKey}
        onOpenFile={onOpenFile} active={visible} refreshKey={`${refresh}:${revision}`} location={location} />)}
      {ROOT_SOURCES.some((source) => listings[source].next !== null) ? <button type="button" className="file-load-more" disabled={loading} onClick={more}>加载更多文件</button> : null}
    </div>
  </div>;
}
