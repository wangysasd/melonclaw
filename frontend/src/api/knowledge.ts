import { requestHeaders } from "./auth";
import { API_BASE_URL, apiRequest, parseErrorResponse } from "./client";

export type KnowledgeRole = "viewer" | "editor" | "owner" | "system_admin";
export type KnowledgeMode = "vector";
export type KnowledgeShareScope = "everyone" | "tenant" | "selected";
export const shareScopeNames: Record<KnowledgeShareScope, string> = { everyone: "所有人", tenant: "同租户", selected: "指定人" };
export interface KnowledgeBase {
  id: string; owner_user_id: string; name: string; description: string;
  status: string; version: number; role: KnowledgeRole; share_scope: KnowledgeShareScope;
  knowledge_mode: KnowledgeMode; active_pipeline_job_id: string | null;
  published_document_count?: number;
  chunk_config: { chunk_size: number; chunk_overlap: number } | null;
}
export interface KnowledgeMember { user_id: string; user_name_zh: string; tenant_name_zh: string; role: "viewer" | "editor" }
export interface KnowledgeCandidate { user_id: string; user_name_zh: string; tenant_name_zh: string }
export const roleNames: Record<KnowledgeRole, string> = { viewer: "查看者", editor: "编辑者", owner: "所有者", system_admin: "系统管理员" };
export const moderationReasons = {
  suspend: [
    { value: "report_review", label: "接到举报，暂停核查" },
    { value: "risk_hold", label: "发现风险，暂停共享" },
  ],
  restore: [
    { value: "review_passed", label: "复核通过，恢复共享" },
    { value: "report_unfounded", label: "举报不成立，恢复共享" },
  ],
  delete: [
    { value: "confirmed_violation", label: "确认违规，删除知识库" },
  ],
} as const;
export type ModerationAction = keyof typeof moderationReasons;
export interface KnowledgeModerationBase {
  id: string; owner_user_id: string; owner_name_zh: string; name: string; description: string;
  status: "suspended"; share_scope: KnowledgeShareScope; version: number;
  created_at: string; updated_at: string;
}
export interface KnowledgeOperation {
  id: string; created_at: string; actor_user_id: string | null; actor_label: string;
  kb_id: string; kb_name: string; action: string; action_label: string; detail: string;
}
const dispositionActions = new Set(["base_suspended", "base_restored", "base_deleted"]);
export const isDispositionAction = (action: string) => dispositionActions.has(action);
const path = (id: string) => `/api/knowledge-bases/${encodeURIComponent(id)}`;
export const knowledgeApi = {
  search: (id: string, body: KnowledgeSearchInput, signal?: AbortSignal) => apiRequest<KnowledgeSearchResult>(`${path(id)}/search`, { method: "POST", body, signal }),
  documents: (id: string, page: number, signal?: AbortSignal) => apiRequest<{ items: KnowledgeDocument[]; total: number; active_pipeline_job_id: string | null }>(`${path(id)}/documents`, { query: { page, page_size: 20 }, signal }),
  documentAction: (id: string, documentId: string, action: "cancel" | "retry" | "delete", requestId?: string) => apiRequest(`${path(id)}/documents/${documentId}${action === "delete" ? "" : `/${action}`}`, { method: action === "delete" ? "DELETE" : "POST", body: action === "retry" ? { request_id: requestId } : undefined }),
  upload: async (id: string, file: File, requestId: string, documentId?: string) => {
    const body = new FormData(); body.append("file", file); body.append("request_id", requestId);
    if (documentId) body.append("document_id", documentId);
    const headers = requestHeaders();
    const response = await fetch(`${API_BASE_URL}${path(id)}/documents`, { method: "POST", cache: "no-store", headers, body });
    if (!response.ok) throw await parseErrorResponse(response, headers.Authorization);
    return response.json();
  },
  download: async (id: string, document: KnowledgeDocument) => {
    const headers = requestHeaders();
    const response = await fetch(`${API_BASE_URL}${path(id)}/documents/${document.id}/source`, { cache: "no-store", headers });
    if (!response.ok) throw await parseErrorResponse(response, headers.Authorization);
    const url = URL.createObjectURL(await response.blob());
    const link = window.document.createElement("a"); link.href = url; link.download = document.display_name;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  list: (page: number, search: string, signal?: AbortSignal, pageSize = 20) => apiRequest<{ items: KnowledgeBase[]; total: number }>("/api/knowledge-bases", { query: { page, page_size: pageSize, search }, signal }),
  detail: (id: string) => apiRequest<KnowledgeBase>(path(id)),
  create: (body: unknown) => apiRequest<KnowledgeBase>("/api/knowledge-bases", { method: "POST", body }),
  update: (id: string, body: unknown) => apiRequest<KnowledgeBase>(path(id), { method: "PATCH", body }),
  delete: (id: string, version: number) => apiRequest(path(id), { method: "DELETE", query: { version } }),
  suspend: (id: string, version: number, reason: string) => apiRequest<{ id: string; status: string; version: number }>(`${path(id)}/suspend`, { method: "POST", body: { version, reason } }),
  restore: (id: string, version: number, reason: string) => apiRequest<{ id: string; status: string; version: number }>(`${path(id)}/restore`, { method: "POST", body: { version, reason } }),
  dispose: (id: string, version: number, reason: string) => apiRequest<{ id: string; status: string; version: number }>(`${path(id)}/dispose`, { method: "POST", body: { version, reason } }),
  suspended: (page: number, search: string, signal?: AbortSignal) => apiRequest<{ items: KnowledgeModerationBase[]; total: number }>("/api/knowledge-moderation/bases", { query: { page, page_size: 20, search }, signal }),
  operations: (id: string, page = 1, signal?: AbortSignal) => apiRequest<{ kb_id: string; name: string; status: string; items: KnowledgeOperation[]; total: number; page: number; page_size: number }>(`${path(id)}/operations`, { query: { page, page_size: 20 }, signal }),
  members: (id: string) => apiRequest<{ items: KnowledgeMember[] }>(`${path(id)}/members`),
  candidates: (id: string, search = "") => apiRequest<{ items: KnowledgeCandidate[] }>(`${path(id)}/member-candidates`, { query: { search } }),
  userCandidates: (search = "") => apiRequest<{ items: KnowledgeCandidate[] }>("/api/knowledge-user-candidates", { query: { search } }),
  setShareScope: (id: string, shareScope: KnowledgeShareScope, version: number) => apiRequest<KnowledgeBase>(`${path(id)}/share-scope`, { method: "PUT", body: { share_scope: shareScope, version } }),
  saveMember: (id: string, target: string, role: string) => apiRequest(`${path(id)}/members/${encodeURIComponent(target)}`, { method: "PUT", body: { role } }),
  revoke: (id: string, target: string) => apiRequest(`${path(id)}/members/${encodeURIComponent(target)}`, { method: "DELETE" }),
  transfer: (id: string, target: string, version: number) => apiRequest(`${path(id)}/transfer`, { method: "POST", body: { target_user_id: target, version } }),
};

export interface KnowledgeDocumentJob {
  id: string; operation?: string; status: string; stage: string | null;
  error_code?: string | null; error_summary: string | null; created_at?: string;
}
export interface KnowledgeDocument {
  id: string; display_name: string; active_revision_id: string | null;
  source_job?: KnowledgeDocumentJob | null;
}

export interface KnowledgeDocumentText {
  kb_id: string;
  document_id: string;
  revision_id: string;
  title: string;
  content: string;
  offset: number;
  next_offset: number | null;
  total_chars: number;
  is_current: boolean;
  revision_no: number;
}

export function readKnowledgeDocumentText(
  kbId: string,
  documentId: string,
  input: { revisionId: string; offset: number; limit: number },
  signal?: AbortSignal,
): Promise<KnowledgeDocumentText> {
  return apiRequest<KnowledgeDocumentText>(
    `${path(kbId)}/documents/${encodeURIComponent(documentId)}/text`,
    { query: { revision_id: input.revisionId, offset: input.offset, limit: input.limit }, signal },
  );
}

export interface KnowledgeSearchInput {
  query: string;
  mode: "vector" | "keyword" | "hybrid";
  top_k: number;
  document_ids: string[];
}
export interface KnowledgeSearchItem {
  kind: "source_chunk";
  chunk_id: string; document_id: string; revision_id: string; title: string;
  content: string; score: number; matched_terms?: number;
  rrf_score?: number; vector_score?: number; keyword_score?: number;
  vector_rank?: number; keyword_rank?: number;
  start_offset: number; snippet_end_offset: number;
  source_locator?: { page?: number; method?: string; kind?: string; block?: number; row?: number; sheet?: string; slide?: number; shape?: number };
  source_url: string;
}
export interface KnowledgeSearchBranch {
  status: "ok" | "failed" | "skipped";
  error_code: string | null;
  requested: number;
  candidate_limit?: number;
  candidates: number;
  returned: number;
  below_threshold?: number;
  duration_ms?: number;
}
export interface KnowledgeSearchResult {
  status: "ok" | "degraded";
  branches: Partial<Record<"vector" | "keyword" | "fused" | "sources", KnowledgeSearchBranch>>;
  items: KnowledgeSearchItem[];
  mode: "vector" | "keyword" | "hybrid";
  notice?: string;
  duration_ms?: number;
}

