import { API_BASE_URL, apiRequest, attachmentContentUrl, getAttachment, parseErrorResponse } from "./client";
import type { AssetRef } from "../lib/resultBlocks";
import { fileRefPath, type FileRef } from "../lib/workspaceFiles";
import type { AttachmentSummary } from "../types/api";
import type { ResultScope } from "../components/ResultContext";

export type ResultFileMetadata = { file_name: string; size_bytes: number; media_type: string; preview_kind: "image" | "text" | "pdf" | "html" | null };
export type ConversationArtifact = { ref: AssetRef; message_id: string; created_at: string };
export function conversationArtifacts(conversationId: string, userId: string, signal: AbortSignal) {
  return apiRequest<{ items: ConversationArtifact[] }>(`/api/conversations/${encodeURIComponent(conversationId)}/artifacts`, { query: { user_id: userId }, signal });
}
export function htmlPreviewUrl(ref: FileRef, scope: ResultScope): string {
  if ("attachment_id" in ref) throw new Error("HTML 预览只支持工作区文件。");
  const query = new URLSearchParams({ user_id: scope.userId, path: fileRefPath(ref) });
  return `${API_BASE_URL}/api/conversations/${encodeURIComponent(scope.conversationId)}/${"workspace_path" in ref ? "files" : "result-files"}/html-preview?${query}`;
}
export const HTML_SOURCE_MARKER = "<!--melonclaw-preview-source-->";
export async function resultFileMetadata(ref: FileRef, scope: ResultScope, signal: AbortSignal): Promise<ResultFileMetadata> {
  if ("attachment_id" in ref) {
    const metadata = await getAttachment(ref.attachment_id, { userId: scope.userId, projectId: scope.projectId, conversationId: scope.projectId ? null : scope.conversationId }, signal);
    return { file_name: metadata.file_name, size_bytes: metadata.size_bytes, media_type: metadata.media_type,
      preview_kind: metadata.kind === "image" ? "image" : metadata.kind === "pdf" ? "pdf" : metadata.kind === "text" && metadata.size_bytes <= 200_000 ? "text" : null };
  }
  return apiRequest<ResultFileMetadata>(`/api/conversations/${encodeURIComponent(scope.conversationId)}/${"workspace_path" in ref ? "files/metadata" : "result-files"}`, { query: { user_id: scope.userId, path: fileRefPath(ref) }, signal });
}
export function resultFileUrl(ref: FileRef, scope: ResultScope, preview = false): string {
  if ("attachment_id" in ref) return attachmentContentUrl(ref.attachment_id, { userId: scope.userId, projectId: scope.projectId, conversationId: scope.projectId ? null : scope.conversationId });
  const query = new URLSearchParams({ user_id: scope.userId, path: fileRefPath(ref), preview: String(preview) });
  return `${API_BASE_URL}/api/conversations/${encodeURIComponent(scope.conversationId)}/${"workspace_path" in ref ? "files" : "result-files"}/content?${query}`;
}

export type DirectoryEntry = { name: string; path: string; kind: "directory" | "file"; size_bytes: number | null; modified_at: string };
export type DirectoryPage = { path: string; items: DirectoryEntry[]; total: number; next_offset: number | null; scope: { kind: "project" | "conversation"; name: string } };
export type FileListQuery = { q: string; sort: "name" | "modified"; offset: number };
export function workspaceDirectory(scope: ResultScope, path: string, query: FileListQuery, signal: AbortSignal) {
  return apiRequest<DirectoryPage>(`/api/conversations/${encodeURIComponent(scope.conversationId)}/files`, { query: { user_id: scope.userId, path, ...query }, signal });
}
export function workspaceAttachments(scope: ResultScope, query: FileListQuery, signal: AbortSignal) {
  return apiRequest<{ items: (AttachmentSummary & { modified_at: string })[]; next_offset: number | null }>(`/api/conversations/${encodeURIComponent(scope.conversationId)}/files/attachments`, { query: { user_id: scope.userId, ...query }, signal });
}
export async function fetchResultBlob(url: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(url, { signal, credentials: "include" });
  if (!response.ok) throw await parseErrorResponse(response);
  return response.blob();
}
