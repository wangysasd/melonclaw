import type {
  ConversationHistory,
  ConversationSummary,
  CreateConversationInput,
  CreateProjectInput,
  DevUser,
  ListConversationsInput,
  ListMessagesInput,
  ManageableModel,
  ManageableProvider,
  ManageableSkill,
  McpServer,
  ModelCatalog,
  Project,
  SkillImportDraft,
  SkillContentPreview,
  SkillOption,
  ServiceStatus,
  AttachmentCapabilities,
  AttachmentSummary,
} from "../types/api";

/**
 * REST 客户端：统一 baseURL、查询参数、错误归一化。
 *
 * VITE_API_BASE_URL 为空时使用同源请求：开发期由 Vite 代理 /api，
 * 生产期由 Nginx/Node 静态服务反代 /api；仅在前后端不同源部署时需要配置。
 */
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? ""
).replace(/\/+$/, "");

/** 后端错误响应统一为 { "error": "<脱敏文案>" }；503 未就绪时可能返回 status 对象。 */
export class ApiError extends Error {
  readonly status: number;
  readonly errorCode?: string;

  constructor(status: number, message: string, errorCode?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.errorCode = errorCode;
  }
}

interface ApiRequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  query?: Record<string, string | number | undefined | null>;
  body?: unknown;
  signal?: AbortSignal;
}

export async function parseErrorResponse(response: Response): Promise<ApiError> {
  let message = `请求失败（${response.status}）。`;
  let errorCode: string | undefined;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const record = body as Record<string, unknown>;
      const text = record.error ?? record.message;
      if (typeof text === "string" && text.trim()) {
        message = text;
      }
      if (typeof record.error_code === "string") errorCode = record.error_code;
    }
  } catch {
    // 响应体不是 JSON 时保留默认文案。
  }
  return new ApiError(response.status, message, errorCode);
}

function buildQuery(
  query: ApiRequestOptions["query"],
): string {
  if (!query) {
    return "";
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const url = `${API_BASE_URL}${path}${buildQuery(options.query)}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers:
        options.body !== undefined
          ? { "Content-Type": "application/json" }
          : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(0, "无法连接到 MelonClaw 服务，请检查网络或服务状态。");
  }
  if (!response.ok) {
    throw await parseErrorResponse(response);
  }
  return (await response.json()) as T;
}

/* ---------- 端点封装 ---------- */

export function getStatus(signal?: AbortSignal): Promise<ServiceStatus> {
  return apiRequest<ServiceStatus>("/api/status", { signal });
}

export function listModels(
  input: { userId: string },
  signal?: AbortSignal,
): Promise<ModelCatalog> {
  return apiRequest<ModelCatalog>("/api/models", {
    query: { user_id: input.userId },
    signal,
  });
}

export function listSkills(
  input: { userId: string },
  signal?: AbortSignal,
): Promise<{ items: SkillOption[] }> {
  return apiRequest<{ items: SkillOption[] }>("/api/skills", {
    query: { user_id: input.userId },
    signal,
  });
}

export function listManageableSkills(input: {
  userId: string;
}): Promise<{ items: ManageableSkill[] }> {
  return apiRequest<{ items: ManageableSkill[] }>("/api/skills/manage", {
    query: { user_id: input.userId },
  });
}

export async function downloadSkill(
  name: string,
  input: { userId: string; scope: "global" | "user" },
): Promise<void> {
  const url = `${API_BASE_URL}/api/skills/${encodeURIComponent(name)}/download${buildQuery({ user_id: input.userId, scope: input.scope })}`;
  let response: Response;
  try {
    response = await fetch(url);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "无法连接到 MelonClaw 服务，请检查网络或服务状态。");
  }
  if (!response.ok) throw await parseErrorResponse(response);

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `${name}.zip`;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

/** POST /api/skills/import/prepare：multipart ZIP 上传，返回待确认草稿。 */
export async function prepareSkillImport(input: {
  userId: string;
  file: File;
  targetId?: string;
}): Promise<SkillImportDraft> {
  const form = new FormData();
  form.append("user_id", input.userId);
  form.append("file", input.file);
  if (input.targetId) form.append("target_id", input.targetId);
  const response = await fetch(`${API_BASE_URL}/api/skills/import/prepare`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) throw await parseErrorResponse(response);
  return (await response.json()) as SkillImportDraft;
}

export function prepareRemoteSkillInstall(input: {
  userId: string;
  repo: string;
  targetId?: string;
}, signal?: AbortSignal): Promise<SkillImportDraft> {
  return apiRequest<SkillImportDraft>("/api/skills/install/remote", {
    method: "POST",
    body: { user_id: input.userId, repo: input.repo, target_id: input.targetId },
    signal,
  });
}

export function confirmSkillImport(input: {
  userId: string;
  draftId: string;
}): Promise<{ ok: boolean }> {
  return apiRequest("/api/skills/import/confirm", {
    method: "POST",
    body: { user_id: input.userId, draft_id: input.draftId },
  });
}

export function cancelSkillImport(input: {
  userId: string;
  draftId: string;
}): Promise<{ ok: boolean }> {
  return apiRequest("/api/skills/import/cancel", {
    method: "POST",
    body: { user_id: input.userId, draft_id: input.draftId },
  });
}

export function updateSkill(
  name: string,
  input: { userId: string; enabled: boolean; scope: "global" | "user" },
): Promise<{ ok: boolean }> {
  return apiRequest(`/api/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: { user_id: input.userId, enabled: input.enabled, scope: input.scope },
  });
}

/** 全员启停共享 Skill；仅 admin/owner。 */
export function updateSkillGlobalState(
  name: string,
  input: { userId: string; enabled: boolean },
): Promise<{ ok: boolean }> {
  return apiRequest(
    `/api/skills/${encodeURIComponent(name)}/global-state`,
    {
      method: "PATCH",
      body: { user_id: input.userId, enabled: input.enabled },
    },
  );
}

export function deleteSkill(
  name: string,
  input: { userId: string; scope: "global" | "user" },
): Promise<{ ok: boolean }> {
  return apiRequest(`/api/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
    query: { user_id: input.userId, scope: input.scope },
  });
}

export function skillDetails(name: string, input: { userId: string; scope: "global" | "user" }): Promise<SkillContentPreview> {
  return apiRequest(`/api/skills/${encodeURIComponent(name)}/details`, {
    query: { user_id: input.userId, scope: input.scope },
  });
}

export function recoverSkills(userId: string): Promise<{ summary: string; missing: string[]; orphaned: string[]; registered: string[] }> {
  return apiRequest("/api/skills/recover", { method: "POST", query: { user_id: userId } });
}

export function listMcp(
  input: { userId: string },
  signal?: AbortSignal,
): Promise<{ items: McpServer[] }> {
  return apiRequest<{ items: McpServer[] }>("/api/mcp", {
    query: { user_id: input.userId },
    signal,
  });
}

export function listManageableModels(
  input: { userId: string },
  signal?: AbortSignal,
): Promise<{ items: ManageableModel[] }> {
  return apiRequest<{ items: ManageableModel[] }>("/api/models/manage", {
    query: { user_id: input.userId },
    signal,
  });
}

export function createModel(input: {
  userId: string;
  modelKey: string;
  providerKey: string;
  scope: "global" | "user";
  displayName: string;
  modelName: string;
  enabled?: boolean;
}): Promise<{ ok: boolean }> {
  return apiRequest("/api/models", {
    method: "POST",
    body: {
      user_id: input.userId,
      model_key: input.modelKey,
      provider_key: input.providerKey,
      scope: input.scope,
      display_name: input.displayName,
      model_name: input.modelName,
      enabled: input.enabled ?? true,
    },
  });
}

export function updateModel(
  modelKey: string,
  input: {
    userId: string;
    enabled?: boolean;
    displayName?: string;
    modelName?: string;
    /** 设为平台默认模型（仅管理员、global scope）。 */
    isDefault?: boolean;
  },
): Promise<{ ok: boolean }> {
  return apiRequest(`/api/models/${encodeURIComponent(modelKey)}`, {
    method: "PATCH",
    body: {
      user_id: input.userId,
      enabled: input.enabled,
      display_name: input.displayName,
      model_name: input.modelName,
      is_default: input.isDefault,
    },
  });
}

export function deleteModel(
  modelKey: string,
  input: { userId: string },
): Promise<{ ok: boolean }> {
  return apiRequest(`/api/models/${encodeURIComponent(modelKey)}`, {
    method: "DELETE",
    query: { user_id: input.userId },
  });
}

export function listManageableProviders(
  input: { userId: string },
  signal?: AbortSignal,
): Promise<{ items: ManageableProvider[] }> {
  return apiRequest<{ items: ManageableProvider[] }>("/api/model-providers", {
    query: { user_id: input.userId },
    signal,
  });
}

export function createProvider(input: {
  userId: string;
  providerKey: string;
  scope: "global";
  displayName: string;
  providerType?: string;
  baseUrl: string;
  apiKey?: string | null;
  modelsEndpoint?: string | null;
  apiKeyEnv?: string;
  requestHeaders?: Record<string, string>;
  extraConfig?: Record<string, unknown>;
  enabled?: boolean;
}): Promise<{ ok: boolean }> {
  return apiRequest("/api/model-providers", {
    method: "POST",
    body: {
      user_id: input.userId,
      provider_key: input.providerKey,
      scope: input.scope,
      display_name: input.displayName,
      provider_type: input.providerType ?? "openai_compatible",
      base_url: input.baseUrl,
      api_key: input.apiKey ?? null,
      models_endpoint: input.modelsEndpoint ?? null,
      api_key_env: input.apiKeyEnv,
      request_headers: input.requestHeaders,
      extra_config: input.extraConfig,
      enabled: input.enabled ?? true,
    },
  });
}

export function updateProvider(
  providerKey: string,
  input: {
    userId: string;
    enabled?: boolean;
    displayName?: string;
    baseUrl?: string;
    apiKey?: string | null;
    modelsEndpoint?: string | null;
    apiKeyEnv?: string;
    requestHeaders?: Record<string, string>;
    extraConfig?: Record<string, unknown>;
  },
): Promise<{ ok: boolean }> {
  return apiRequest(
    `/api/model-providers/${encodeURIComponent(providerKey)}`,
    {
      method: "PATCH",
      body: {
        user_id: input.userId,
        enabled: input.enabled,
        display_name: input.displayName,
        base_url: input.baseUrl,
        api_key: input.apiKey ?? null,
        models_endpoint: input.modelsEndpoint ?? null,
        api_key_env: input.apiKeyEnv,
        request_headers: input.requestHeaders,
        extra_config: input.extraConfig,
      },
    },
  );
}

export function deleteProvider(
  providerKey: string,
  input: { userId: string },
): Promise<{ ok: boolean }> {
  return apiRequest(
    `/api/model-providers/${encodeURIComponent(providerKey)}`,
    {
      method: "DELETE",
      query: { user_id: input.userId },
    },
  );
}

/** 实时拉取供应商远端 /models 清单，不落库。 */
export function fetchRemoteModels(
  providerKey: string,
  input: { userId: string },
): Promise<{ items: { id: string; display_name: string }[] }> {
  return apiRequest<{ items: { id: string; display_name: string }[] }>(
    `/api/model-providers/${encodeURIComponent(providerKey)}/remote-models`,
    { query: { user_id: input.userId } },
  );
}

/** 临时测试当前供应商表单的远端 /models 端点，不保存配置或凭据。 */
export function testProviderConnection(input: {
  userId: string;
  providerKey?: string;
  baseUrl: string;
  modelsEndpoint: string;
  apiKey?: string;
  apiKeyEnv: string;
  requestHeaders?: Record<string, string>;
}): Promise<{ model_count: number }> {
  return apiRequest<{ model_count: number }>("/api/model-providers/test-connection", {
    method: "POST",
    body: {
      user_id: input.userId,
      provider_key: input.providerKey,
      base_url: input.baseUrl,
      models_endpoint: input.modelsEndpoint,
      api_key: input.apiKey,
      api_key_env: input.apiKeyEnv,
      request_headers: input.requestHeaders,
    },
  });
}

/** 普通用户在共享供应商上设置自己的 Key；只写不回读。 */
export function setMyProviderKey(
  providerKey: string,
  input: { userId: string; apiKey: string },
): Promise<{ ok: boolean }> {
  return apiRequest(
    `/api/model-providers/${encodeURIComponent(providerKey)}/my-key`,
    {
      method: "PUT",
      body: { user_id: input.userId, api_key: input.apiKey },
    },
  );
}

/** 清除自己的 Key 覆盖，回落到共享 Key。 */
export function deleteMyProviderKey(
  providerKey: string,
  input: { userId: string },
): Promise<{ ok: boolean }> {
  return apiRequest(
    `/api/model-providers/${encodeURIComponent(providerKey)}/my-key`,
    {
      method: "DELETE",
      query: { user_id: input.userId },
    },
  );
}

export function listDevUsers(
  signal?: AbortSignal,
): Promise<{ items: DevUser[] }> {
  return apiRequest<{ items: DevUser[] }>("/api/dev/users", { signal });
}

export function createDevUser(input: {
  actorUserId: string;
  userId: string;
  userNameZh: string;
}): Promise<{ ok: boolean }> {
  return apiRequest("/api/dev/users", {
    method: "POST",
    body: {
      actor_user_id: input.actorUserId,
      user_id: input.userId,
      user_name_zh: input.userNameZh,
    },
  });
}

export function listProjects(
  input: { userId: string },
  signal?: AbortSignal,
): Promise<{ items: Project[] }> {
  return apiRequest<{ items: Project[] }>("/api/projects", {
    query: { user_id: input.userId },
    signal,
  });
}

export function createProject(input: CreateProjectInput): Promise<Project> {
  return apiRequest<Project>("/api/projects", {
    method: "POST",
    body: {
      user_id: input.userId,
      name: input.name,
    },
  });
}

type ResourceIdentity = { userId: string };

export function updateProject(id: string, input: ResourceIdentity & { name?: string; isPinned?: boolean }): Promise<Project> {
  return apiRequest<Project>(`/api/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: { user_id: input.userId, name: input.name, is_pinned: input.isPinned },
  });
}

export function deleteProject(id: string, input: ResourceIdentity): Promise<{ deleted: boolean }> {
  return apiRequest(`/api/projects/${encodeURIComponent(id)}`, {
    method: "DELETE", query: { user_id: input.userId },
  });
}

export function listConversations(
  input: ListConversationsInput,
  signal?: AbortSignal,
): Promise<{ items: ConversationSummary[]; next_cursor: string | null }> {
  return apiRequest<{
    items: ConversationSummary[];
    next_cursor: string | null;
  }>("/api/conversations", {
    query: {
      user_id: input.userId,
      project_id: input.projectId,
      scope: input.scope,
      limit: input.limit ?? 10,
      cursor: input.cursor,
    },
    signal,
  });
}

export function createConversation(
  input: CreateConversationInput,
): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>("/api/conversations", {
    method: "POST",
    body: {
      user_id: input.userId,
      project_id: input.projectId,
    },
  });
}

export function updateConversation(id: string, input: ResourceIdentity & { name?: string; isPinned?: boolean }): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: { user_id: input.userId, name: input.name, is_pinned: input.isPinned },
  });
}

export function moveConversationToProject(
  id: string,
  input: ResourceIdentity & { projectId: string },
): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>(`/api/conversations/${encodeURIComponent(id)}/move-to-project`, {
    method: "POST",
    body: { user_id: input.userId, project_id: input.projectId },
  });
}

export function deleteConversation(id: string, input: ResourceIdentity): Promise<{ deleted: boolean }> {
  return apiRequest(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE", query: { user_id: input.userId },
  });
}

export function getConversationHistory(
  input: ListMessagesInput,
  signal?: AbortSignal,
): Promise<ConversationHistory> {
  return apiRequest<ConversationHistory>(
    `/api/conversations/${encodeURIComponent(input.conversationId)}/messages`,
    {
      query: {
        user_id: input.userId,
        limit: input.limit ?? 50,
        before_seq: input.beforeSeq,
      },
      signal,
    },
  );
}

export interface UploadedAttachment extends AttachmentSummary {
  derived_size_bytes?: number;
}

type AttachmentAccessInput = {
  userId: string;
  projectId?: string | null;
  conversationId?: string | null;
};

function attachmentAccessQuery(input: AttachmentAccessInput) {
  return {
    user_id: input.userId,
    project_id: input.projectId,
    conversation_id: input.conversationId,
  };
}

/** GET /api/attachments/capabilities：类型与限制的单一来源。 */
export function getAttachmentCapabilities(
  signal?: AbortSignal,
): Promise<AttachmentCapabilities> {
  return apiRequest<AttachmentCapabilities>("/api/attachments/capabilities", {
    signal,
  });
}

function abortError(): DOMException {
  return new DOMException("请求已取消。", "AbortError");
}

function parseXhrError(request: XMLHttpRequest): ApiError {
  let message = `请求失败（${request.status}）。`;
  let errorCode: string | undefined;
  try {
    const body: unknown = JSON.parse(request.responseText);
    if (body && typeof body === "object") {
      const record = body as Record<string, unknown>;
      const text = record.error ?? record.message;
      if (typeof text === "string" && text.trim()) message = text;
      if (typeof record.error_code === "string") errorCode = record.error_code;
    }
  } catch {
    // 响应体不是 JSON 时保留默认文案。
  }
  return new ApiError(request.status, message, errorCode);
}

/**
 * 上传附件。使用 XMLHttpRequest 而不是 fetch，因为只有 XHR 能上报
 * 上传进度（fetch 至今没有上传方向的进度事件）。
 */
export function uploadAttachment(
  target: { projectId: string } | { conversationId: string },
  input: {
    userId: string;
    file: File;
    clientRequestId: string;
    onProgress?: (percent: number) => void;
  },
  signal?: AbortSignal,
): Promise<UploadedAttachment> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("user_id", input.userId);
  form.append("client_request_id", input.clientRequestId);
  const url = "projectId" in target
    ? `${API_BASE_URL}/api/projects/${encodeURIComponent(target.projectId)}/attachments`
    : `${API_BASE_URL}/api/conversations/${encodeURIComponent(target.conversationId)}/attachments`;
  return new Promise<UploadedAttachment>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const request = new XMLHttpRequest();
    request.open("POST", url);
    const handleAbort = () => request.abort();
    signal?.addEventListener("abort", handleAbort, { once: true });
    const detach = () => signal?.removeEventListener("abort", handleAbort);
    request.upload.onprogress = (event) => {
      if (!input.onProgress || !event.lengthComputable || event.total <= 0) return;
      input.onProgress(
        Math.min(100, Math.max(0, Math.round((event.loaded / event.total) * 100))),
      );
    };
    request.onload = () => {
      detach();
      if (request.status >= 200 && request.status < 300) {
        try {
          resolve(JSON.parse(request.responseText) as UploadedAttachment);
        } catch {
          reject(new ApiError(request.status, "附件上传响应无法解析。"));
        }
        return;
      }
      reject(parseXhrError(request));
    };
    request.onerror = () => {
      detach();
      reject(new ApiError(0, "无法连接到 MelonClaw 服务，请检查网络或服务状态。"));
    };
    request.onabort = () => {
      detach();
      reject(abortError());
    };
    request.send(form);
  });
}

/** POST /api/attachments/{id}/parse：重置解析失败的 staged 附件并重新排队。 */
export function retryAttachmentParse(
  attachmentId: string,
  input: AttachmentAccessInput,
): Promise<UploadedAttachment> {
  return apiRequest<UploadedAttachment>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/parse`,
    {
      method: "POST",
      query: attachmentAccessQuery(input),
    },
  );
}

export function getAttachment(
  attachmentId: string,
  input: AttachmentAccessInput,
  signal?: AbortSignal,
): Promise<UploadedAttachment> {
  return apiRequest<UploadedAttachment>(
    `/api/attachments/${encodeURIComponent(attachmentId)}`,
    { query: attachmentAccessQuery(input), signal },
  );
}

export function deleteAttachment(
  attachmentId: string,
  input: AttachmentAccessInput,
): Promise<UploadedAttachment> {
  return apiRequest<UploadedAttachment>(
    `/api/attachments/${encodeURIComponent(attachmentId)}`,
    {
      method: "DELETE",
      query: attachmentAccessQuery(input),
    },
  );
}

export function attachmentContentUrl(
  attachmentId: string,
  input: AttachmentAccessInput,
): string {
  const params = new URLSearchParams({ user_id: input.userId });
  if (input.projectId) params.set("project_id", input.projectId);
  if (input.conversationId) params.set("conversation_id", input.conversationId);
  return `${API_BASE_URL}/api/attachments/${encodeURIComponent(attachmentId)}/content?${params.toString()}`;
}
