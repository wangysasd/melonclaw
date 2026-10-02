import { API_BASE_URL, apiRequest, attachmentContentUrl, getAttachment, parseErrorResponse } from "./client";
import type { AssetRef } from "../lib/resultBlocks";
import type { ResultScope } from "../components/ResultContext";

export type ResultFileMetadata = { file_name: string; size_bytes: number; media_type: string; preview_kind: "image" | "text" | "pdf" | null };
export async function resultFileMetadata(ref: AssetRef, scope: ResultScope, signal: AbortSignal): Promise<ResultFileMetadata> {
  if ("attachment_id" in ref) {
    const metadata = await getAttachment(ref.attachment_id, { userId: scope.userId, projectId: scope.projectId, conversationId: scope.projectId ? null : scope.conversationId }, signal);
    return { file_name: metadata.file_name, size_bytes: metadata.size_bytes, media_type: metadata.media_type,
      preview_kind: metadata.kind === "image" ? "image" : metadata.kind === "pdf" ? "pdf" : metadata.kind === "text" && metadata.size_bytes <= 200_000 ? "text" : null };
  }
  return apiRequest<ResultFileMetadata>(`/api/conversations/${encodeURIComponent(scope.conversationId)}/result-files`, { query: { user_id: scope.userId, path: ref.path }, signal });
}
export function resultFileUrl(ref: AssetRef, scope: ResultScope, preview = false): string {
  if ("attachment_id" in ref) return attachmentContentUrl(ref.attachment_id, { userId: scope.userId, projectId: scope.projectId, conversationId: scope.projectId ? null : scope.conversationId });
  const query = new URLSearchParams({ user_id: scope.userId, path: ref.path, preview: String(preview) });
  return `${API_BASE_URL}/api/conversations/${encodeURIComponent(scope.conversationId)}/result-files/content?${query}`;
}
export async function fetchResultBlob(url: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw await parseErrorResponse(response);
  return response.blob();
}
