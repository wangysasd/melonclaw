import { apiRequest } from "./client";

export const reportReasons = {
  cannot_login: "无法登录或无法使用",
  illegal: "违法不良信息",
  harmful: "有害或不当内容",
  privacy: "侵犯隐私",
  infringement: "侵权",
  misinformation: "不实信息",
  abuse: "骚扰或滥用",
  other: "其他",
} as const;

export type ReportReason = keyof typeof reportReasons;
export const generalReasons = [
  "cannot_login", "illegal", "harmful", "privacy", "infringement", "misinformation", "abuse", "other",
] as const;
export const reportStatusNames = {
  pending: "待受理",
  processing: "处理中",
  resolved: "已办结",
} as const;
export type ReportStatus = keyof typeof reportStatusNames;

export interface ReportChannels {
  email?: string;
  contact_name?: string;
  response_hours?: number;
}

export interface ReportTargetView {
  kind: "general" | "assistant_message" | "knowledge_document";
  label: string;
  message_seq?: number | null;
  revision_no?: number | null;
}

export interface ReportEvent {
  action: string;
  created_at: string;
  feedback?: string | null;
  internal_note?: string | null;
  measure?: string | null;
  actor_user_id?: string | null;
}

export interface ReportSummary {
  public_code: string;
  status: ReportStatus;
  kind: string;
  reason: ReportReason;
  target: ReportTargetView;
  has_feedback: boolean;
  updated_at: string;
  created_at: string;
}

export interface ReportDetail extends ReportSummary {
  description: string;
  contact: string | null;
  public_feedback: string | null;
  events: ReportEvent[];
}

export interface ReportSubmitResult {
  public_code: string;
  status: ReportStatus;
  notice: string;
  lookup_code?: string;
}

export interface ReportLive {
  available: boolean;
  hash_matches: boolean;
  superseded?: boolean;
  status?: string | null;
  message_seq?: number | null;
  revision_no?: number | null;
  published_at?: string | null;
}

export interface AdminReportTarget {
  kind: string;
  label: string;
  conversation_id?: string;
  message_id?: string;
  message_seq?: number | null;
  message_created_at?: string | null;
  kb_id?: string;
  document_id?: string;
  revision_id?: string;
  revision_no?: number | null;
  source_hash?: string;
  char_count?: number | null;
  excerpt?: string;
  live?: ReportLive | null;
}

export interface AdminReportSummary {
  id: string;
  public_code: string;
  status: ReportStatus;
  kind: string;
  reason: ReportReason;
  target_label: string;
  submitter_user_id: string | null;
  updated_at: string;
  created_at: string;
}

export interface AdminReportDetail {
  id: string;
  public_code: string;
  status: ReportStatus;
  kind: string;
  reason: ReportReason;
  description: string;
  contact: string | null;
  submitter_user_id: string | null;
  assignee_user_id: string | null;
  public_feedback: string | null;
  measure: string | null;
  target: AdminReportTarget;
  created_at: string;
  updated_at: string;
  accepted_at: string | null;
  resolved_at: string | null;
  events: ReportEvent[];
}

export interface ReportSubmitTarget {
  kind: "general" | "assistant_message" | "knowledge_document";
  conversation_id?: string;
  message_id?: string;
  kb_id?: string;
  document_id?: string;
  revision_id?: string;
}

const eventNames: Record<string, string> = {
  submitted: "已提交",
  accepted: "已受理",
  noted: "已记录内部备注",
  replied: "已反馈",
  resolved: "已办结",
};

export function reportEventName(action: string) {
  return eventNames[action] ?? action;
}

export function reportChannels(signal?: AbortSignal) {
  return apiRequest<ReportChannels>("/api/reports/channels", { signal });
}

export function submitAnonymousReport(body: { reason: ReportReason; description: string; contact?: string }) {
  return apiRequest<ReportSubmitResult>("/api/reports/anonymous", { method: "POST", body });
}

export function lookupReport(body: { public_code: string; lookup_code: string }) {
  return apiRequest<ReportDetail>("/api/reports/lookup", { method: "POST", body });
}

export function submitReport(body: {
  reason: ReportReason;
  description: string;
  contact?: string;
  target: ReportSubmitTarget;
}) {
  return apiRequest<ReportSubmitResult>("/api/reports", { method: "POST", body });
}

export function listMyReports(signal?: AbortSignal) {
  return apiRequest<{ items: ReportSummary[] }>("/api/reports", { signal });
}

export function getMyReport(publicCode: string) {
  return apiRequest<ReportDetail>(`/api/reports/${encodeURIComponent(publicCode)}`);
}

export function listAdminReports(query: { status?: string; code?: string }, signal?: AbortSignal) {
  return apiRequest<{ items: AdminReportSummary[] }>("/api/admin/reports", { query, signal });
}

export function getAdminReport(id: string) {
  return apiRequest<AdminReportDetail>(`/api/admin/reports/${encodeURIComponent(id)}`);
}

export function readReportedMessage(id: string) {
  return apiRequest<{ content: string }>(`/api/admin/reports/${encodeURIComponent(id)}/message`, { method: "POST" });
}

export function acceptReport(id: string) {
  return apiRequest<AdminReportDetail>(`/api/admin/reports/${encodeURIComponent(id)}/accept`, { method: "POST" });
}

export function noteReport(id: string, note: string) {
  return apiRequest<AdminReportDetail>(`/api/admin/reports/${encodeURIComponent(id)}/note`, { method: "POST", body: { note } });
}

export function replyReport(id: string, feedback: string) {
  return apiRequest<AdminReportDetail>(`/api/admin/reports/${encodeURIComponent(id)}/reply`, { method: "POST", body: { feedback } });
}

export function resolveReport(id: string, feedback: string, measure: string) {
  return apiRequest<AdminReportDetail>(`/api/admin/reports/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    body: { feedback, measure },
  });
}
