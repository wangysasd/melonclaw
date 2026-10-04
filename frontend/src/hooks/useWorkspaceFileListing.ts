import { useEffect, useMemo, useRef, useState } from "react";
import type { ResultScope } from "../components/ResultContext";
import type { FileLocation } from "../lib/workspaceFiles";
import {
  containsFileLocation, EMPTY_LISTING, mergeFileRows, readFileListing, ROOT_SOURCES,
  rootSourceForLocation, unifiedFileRows, type FileListing, type RootSource,
} from "../lib/workspaceFileListing";

type SourceListing = FileListing & { loaded: boolean; paged: boolean; retryOffset: number };
type RootListings = Record<RootSource, SourceListing>;
function emptyListings(): RootListings {
  const empty = { ...EMPTY_LISTING, loaded: false, paged: false, retryOffset: 0 };
  return { "/": { ...empty }, attachments: { ...empty }, "/outputs": { ...empty, loading: false } };
}

/** Only the three visible root sources are combined; real subdirectories stay lazy. */
export function useWorkspaceFileListing(scope: ResultScope, visible: boolean, refreshKey: string, location: FileLocation, selectedKey: string) {
  const retained = useRef({ key: "", data: emptyListings() });
  const [listings, setListings] = useState<RootListings>(emptyListings);
  const actions = useRef<{ more: () => void; retry: (source: RootSource) => void }>({ more: () => {}, retry: () => {} });
  const { userId, conversationId, projectId } = scope;
  useEffect(() => {
    if (!visible) return;
    const key = JSON.stringify([userId, conversationId, projectId, refreshKey]);
    const restore = retained.current.key === key;
    let data = restore ? retained.current.data : emptyListings();
    let cancelled = false;
    const requests = new Map<RootSource, AbortController>();
    const identity = { userId, conversationId, projectId, anchorPrefix: "workspace-files" };
    const update = (source: RootSource, value: SourceListing) => {
      data = { ...data, [source]: value };
      retained.current = { key, data };
      setListings(data);
    };
    const hasOutputs = () => data["/"].rows.some((row) => row.kind === "directory" && row.path === "/outputs");
    const target = rootSourceForLocation(location);
    const locate = (source: RootSource) => {
      const listing = data[source];
      const relevant = source === target || (source === "/" && target === "/outputs" && !hasOutputs());
      if (selectedKey && relevant && !listing.error && listing.next !== null
        && !containsFileLocation(listing.rows, location, selectedKey)) void load(source, listing.next);
    };
    const load = async (source: RootSource, offset = 0, silent = false) => {
      if (cancelled || requests.has(source)) return;
      if (!conversationId) {
        update(source, { ...data[source], loading: false, error: "请先选择或创建对话。" });
        return;
      }
      const controller = new AbortController();
      requests.set(source, controller);
      if (!silent) update(source, { ...data[source], loading: true, error: "" });
      try {
        const page = await readFileListing(identity, source, offset, controller.signal);
        if (cancelled || controller.signal.aborted) return;
        update(source, { rows: mergeFileRows(offset ? data[source].rows : [], page.rows), next: page.next_offset,
          loading: false, error: "", loaded: true, paged: offset > 0, retryOffset: 0 });
        if (source === "/") {
          if (hasOutputs()) {
            if (!data["/outputs"].loaded && !data["/outputs"].error) void load("/outputs");
          } else if (page.next_offset === null) {
            requests.get("/outputs")?.abort();
            update("/outputs", emptyListings()["/outputs"]);
          }
        }
      } catch (failure) {
        if (!cancelled && !controller.signal.aborted) update(source, { ...data[source], loading: false,
          error: failure instanceof Error ? failure.message : "文件列表加载失败，请重试。", retryOffset: offset });
      } finally {
        if (requests.get(source) === controller) requests.delete(source);
      }
      if (!cancelled && !controller.signal.aborted) locate(source);
    };
    retained.current = { key, data };
    setListings(data);
    // Attachments and the workspace root have no dependency on each other.
    for (const source of ROOT_SOURCES) {
      if (source === "/outputs" && !hasOutputs()) continue;
      if (!data[source].loaded && !data[source].error) void load(source);
      else locate(source);
    }
    actions.current = {
      more: () => { for (const source of ROOT_SOURCES) if (data[source].next !== null && !data[source].error) void load(source, data[source].next!); },
      retry: (source) => { void load(source, data[source].retryOffset); },
    };
    // Once any source has been paged, explicit refresh preserves all loaded pages.
    const timer = setInterval(() => {
      if (document.hidden || ROOT_SOURCES.some((source) => data[source].paged)) return;
      for (const source of ROOT_SOURCES) {
        if (source === "/outputs" && !hasOutputs()) continue;
        void load(source, 0, true);
      }
    }, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
      for (const controller of requests.values()) controller.abort();
      for (const source of ROOT_SOURCES) data = { ...data, [source]: { ...data[source], loading: false } };
      retained.current = { key, data };
    };
  }, [userId, conversationId, projectId, refreshKey, location, selectedKey, visible]);
  const rows = useMemo(() => unifiedFileRows(listings["/"].rows, listings.attachments.rows, listings["/outputs"].rows), [listings]);
  return { rows, listings, loading: ROOT_SOURCES.some((source) => listings[source].loading),
    more: () => actions.current.more(), retry: (source: RootSource) => actions.current.retry(source) };
}
