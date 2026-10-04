import { workspaceAttachments, workspaceDirectory } from "../api/results";
import type { ResultScope } from "../components/ResultContext";
import { fileRefKey, workspaceFileRef, type FileLocation, type FileRef } from "./workspaceFiles";

export type FileRow = {
  name: string; kind: "directory" | "file"; path?: string; ref?: FileRef;
  source?: "上传" | "输出";
};
export type FileListing = { rows: FileRow[]; next: number | null; loading: boolean; error: string };
export const EMPTY_LISTING: FileListing = { rows: [], next: null, loading: true, error: "" };
export const ROOT_SOURCES = ["/", "attachments", "/outputs"] as const;
export type RootSource = typeof ROOT_SOURCES[number];
export const SOURCE_LABELS: Record<RootSource, string> = { "/": "工作区文件", attachments: "上传附件", "/outputs": "输出文件" };

export function fileRowKey(row: FileRow): string {
  return row.ref ? fileRefKey(row.ref) : row.path!;
}

export function mergeFileRows(previous: FileRow[], incoming: FileRow[]): FileRow[] {
  const rows = new Map<string, FileRow>();
  for (const row of [...previous, ...incoming]) rows.set(fileRowKey(row), row);
  return [...rows.values()];
}

export function unifiedFileRows(root: FileRow[], attachments: FileRow[], outputs: FileRow[]): FileRow[] {
  const rows = mergeFileRows(root.filter((row) => !(row.kind === "directory" && row.path === "/outputs")), [...attachments, ...outputs]);
  return rows.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "directory" ? -1 : 1;
    const left = a.name.toLowerCase();
    const right = b.name.toLowerCase();
    return left < right ? -1 : left > right ? 1 : fileRowKey(a).localeCompare(fileRowKey(b));
  });
}

export function containsFileLocation(rows: FileRow[], location: FileLocation, selectedKey: string): boolean {
  return rows.some((row) => row.ref ? fileRefKey(row.ref) === selectedKey
    : location === row.path || location.startsWith(`${row.path}/`));
}

export function rootSourceForLocation(location: FileLocation): RootSource {
  return location === "attachments" ? "attachments" : location === "/outputs" || location.startsWith("/outputs/") ? "/outputs" : "/";
}

export async function readFileListing(scope: ResultScope, path: FileLocation, offset: number, signal: AbortSignal) {
  const options = { q: "", sort: "name" as const, offset };
  if (path === "attachments") {
    const page = await workspaceAttachments(scope, options, signal);
    return { rows: page.items.map((item): FileRow => ({ name: item.file_name, kind: "file", ref: { attachment_id: item.attachment_id }, source: "上传" })), next_offset: page.next_offset };
  }
  const page = await workspaceDirectory(scope, path, options, signal);
  return { rows: page.items.map((item): FileRow => ({ name: item.name, kind: item.kind, path: item.path,
    ref: item.kind === "file" ? workspaceFileRef(item.path) : undefined,
    source: item.path.startsWith("/outputs/") ? "输出" : undefined })), next_offset: page.next_offset };
}
