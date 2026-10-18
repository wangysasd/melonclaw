import { apiRequest } from "./client";

export interface EnvironmentVariable {
  id: string; name: string; description: string; scope: "global" | "user";
  enabled: boolean; version: number; masked_value: string; updated_at: string;
  effective_source: "personal" | "shared" | "none";
}
export interface EnvironmentFields { value?: string; description?: string; enabled?: boolean }
export function listEnvironment(userId: string, signal?: AbortSignal) {
  return apiRequest<{ items: EnvironmentVariable[]; scope: "global" | "user" }>("/api/environment", {
    query: { user_id: userId }, signal,
  });
}
export function saveEnvironment(userId: string, name: string, fields: EnvironmentFields,
  existing?: EnvironmentVariable, signal?: AbortSignal) {
  return apiRequest(existing ? `/api/environment/${existing.id}` : "/api/environment", {
    method: existing ? "PATCH" : "POST", signal,
    body: { user_id: userId, ...fields, ...(existing ? { expected_version: existing.version } : { name }) },
  });
}
export function deleteEnvironment(userId: string, row: EnvironmentVariable, signal?: AbortSignal) {
  return apiRequest(`/api/environment/${row.id}`, { method: "DELETE", signal,
    query: { user_id: userId, expected_version: row.version } });
}
