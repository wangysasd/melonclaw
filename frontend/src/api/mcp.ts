import { apiRequest } from "./client";
import type { McpConfiguration, McpServer, McpTestResult, McpToolDiscoveryResult } from "../types/api";

export function mcpDetail(id: string, userId: string, signal?: AbortSignal): Promise<McpServer> {
  return apiRequest(`/api/mcp/${id}`, { query: { user_id: userId }, signal });
}
export function saveMcp(userId: string, configuration: McpConfiguration, existing?: McpServer) {
  if (existing) {
    const { slug: _slug, ...fields } = configuration;
    void _slug;
    return apiRequest(`/api/mcp/${existing.id}`, { method: "PATCH", body: { ...fields, user_id: userId, version: existing.version } });
  }
  return apiRequest("/api/mcp", { method: "POST", body: { ...configuration, user_id: userId } });
}
export function testMcp(userId: string, configuration: McpConfiguration, existing: McpServer | undefined, signal: AbortSignal): Promise<McpTestResult> {
  return apiRequest("/api/mcp/test", { method: "POST", signal, body: { ...configuration, user_id: userId,
    base_id: existing?.id ?? null, version: existing?.version ?? null } });
}
export function discoverMcpTools(server: McpServer, userId: string, signal: AbortSignal, options: { background?: boolean; refresh?: boolean } = {}): Promise<McpToolDiscoveryResult> {
  return apiRequest(`/api/mcp/${server.id}/tools`, { method: "POST", signal, body: { user_id: userId, version: server.version, ...options } });
}
export function addMcp(server: McpServer, userId: string) {
  return apiRequest(`/api/mcp/${server.id}/add`, { method: "POST", body: { user_id: userId, version: server.version } });
}
export function stopMcp(server: McpServer, userId: string) {
  return apiRequest(`/api/mcp/preferences/${encodeURIComponent(server.slug)}`, { method: "PUT", body: { user_id: userId, enabled: false } });
}
export function globalMcpState(server: McpServer, userId: string) {
  return apiRequest(`/api/mcp/${server.id}/global-state`, { method: "PUT", body: { user_id: userId, version: server.version, enabled: !server.enabled } });
}
export function deleteMcp(server: McpServer, userId: string) {
  return apiRequest(`/api/mcp/${server.id}`, { method: "DELETE", query: { user_id: userId, version: server.version } });
}
