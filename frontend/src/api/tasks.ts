import { apiRequest } from "./client";

export interface TaskSchedule { kind: "manual" | "once" | "daily" | "weekly"; run_at?: string | null; local_time?: string | null; weekday?: number | null }
export interface TaskDefinition {
  name: string; instruction: string; model_id: string; schedule: TaskSchedule;
}
export interface TaskRun {
  id: string; task_id: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  created_at: string; started_at: string | null; finished_at: string | null;
  cancel_requested_at?: string | null; scheduled_for?: string | null;
  result_is_partial: boolean; result_markdown?: string; error_code?: string; error_message?: string;
  config_snapshot?: { definition: TaskDefinition; task_version: number; model_display_name: string };
}
export interface Task {
  id: string; conversation_id: string; name: string; version: number; status: "enabled" | "paused" | "archived";
  has_active_run?: boolean;
  next_run_at?: string | null;
  definition: TaskDefinition; latest_run?: TaskRun; updated_at: string; pause_reason?: string;
}
export interface TaskStatus { executor: { ready: boolean; running: number; last_scan_at: string | null; error: string | null }; search_available: boolean }
export interface Page<T> { items: T[]; total: number }
export const taskApi = {
  list: (query: { name?: string; status?: string; page: number }, signal: AbortSignal) => apiRequest<Page<Task>>("/api/tasks", { query, signal }),
  status: (signal: AbortSignal) => apiRequest<TaskStatus>("/api/tasks/status", { signal }),
  get: (id: string, signal: AbortSignal) => apiRequest<Task>(`/api/tasks/${id}`, { signal }),
  history: (id: string, page: number, signal: AbortSignal) => apiRequest<Page<TaskRun>>(`/api/tasks/${id}/runs`, { query: { page }, signal }),
  run: (id: string, signal: AbortSignal) => apiRequest<TaskRun>(`/api/task-runs/${id}`, { signal }),
  save: (id: string | undefined, body: { definition: TaskDefinition; enabled: boolean; expected_version?: number; idempotency_key: string; run_now: boolean }) => apiRequest<{ task: Task; run: TaskRun | null }>(id ? `/api/tasks/${id}` : "/api/tasks", { method: id ? "PATCH" : "POST", body }),
  enable: (task: Task, enabled: boolean, key: string) => apiRequest<Task>(`/api/tasks/${task.id}/enabled`, { method: "POST", body: { enabled, expected_version: task.version, idempotency_key: key } }),
  cancel: (runId: string, key: string) => apiRequest<TaskRun>(`/api/task-runs/${runId}/cancel`, { method: "POST", body: { idempotency_key: key } }),
  action: (task: Task, action: "runs" | "archive", key: string) => apiRequest<TaskRun | Task>(`/api/tasks/${task.id}/${action}`, { method: "POST", body: { expected_version: task.version, idempotency_key: key } }),
};
