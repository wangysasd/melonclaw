import type {
  ConversationHistory,
  ConversationSummary,
  CreateConversationInput,
  CreateProjectInput,
  DevUser,
  ListConversationsInput,
  ListMessagesInput,
  ModelCatalog,
  Project,
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
  method?: "GET" | "POST" | "DELETE";
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
  input: { userId: string; tenantId?: string | null },
  signal?: AbortSignal,
): Promise<ModelCatalog> {
  return apiRequest<ModelCatalog>("/api/models", {
    query: { user_id: input.userId, tenant_id: input.tenantId },
    signal,
  });
}

export function listSkills(
  signal?: AbortSignal,
): Promise<{ items: SkillOption[] }> {
  return apiRequest<{ items: SkillOption[] }>("/api/skills", { signal });
}

export function listDevUsers(
  signal?: AbortSignal,
): Promise<{ items: DevUser[] }> {
  return apiRequest<{ items: DevUser[] }>("/api/dev/users", { signal });
}

export function listProjects(
  input: { userId: string; tenantId?: string | null },
  signal?: AbortSignal,
): Promise<{ items: Project[] }> {
  return apiRequest<{ items: Project[] }>("/api/projects", {
    query: { user_id: input.userId, tenant_id: input.tenantId },
    signal,
  });
}

export function createProject(input: CreateProjectInput): Promise<Project> {
  return apiRequest<Project>("/api/projects", {
    method: "POST",
    body: {
      user_id: input.userId,
      tenant_id: input.tenantId ?? null,
      name: input.name,
    },
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
      tenant_id: input.tenantId,
      project_id: input.projectId,
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
      tenant_id: input.tenantId ?? null,
      project_id: input.projectId ?? null,
    },
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
        tenant_id: input.tenantId,
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
  projectId: string,
  input: {
    userId: string;
    tenantId?: string | null;
    file: File;
    clientRequestId: string;
    onProgress?: (percent: number) => void;
  },
  signal?: AbortSignal,
): Promise<UploadedAttachment> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("user_id", input.userId);
  if (input.tenantId) form.append("tenant_id", input.tenantId);
  form.append("client_request_id", input.clientRequestId);
  const url = `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/attachments`;
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
  input: { userId: string; tenantId?: string | null },
): Promise<UploadedAttachment> {
  return apiRequest<UploadedAttachment>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/parse`,
    {
      method: "POST",
      query: { user_id: input.userId, tenant_id: input.tenantId },
    },
  );
}

export function getAttachment(
  attachmentId: string,
  input: { userId: string; tenantId?: string | null },
  signal?: AbortSignal,
): Promise<UploadedAttachment> {
  return apiRequest<UploadedAttachment>(
    `/api/attachments/${encodeURIComponent(attachmentId)}`,
    { query: { user_id: input.userId, tenant_id: input.tenantId }, signal },
  );
}

export function deleteAttachment(
  attachmentId: string,
  input: { userId: string; tenantId?: string | null },
): Promise<UploadedAttachment> {
  return apiRequest<UploadedAttachment>(
    `/api/attachments/${encodeURIComponent(attachmentId)}`,
    {
      method: "DELETE",
      query: { user_id: input.userId, tenant_id: input.tenantId },
    },
  );
}

export function attachmentContentUrl(
  attachmentId: string,
  input: { userId: string; tenantId?: string | null },
): string {
  const params = new URLSearchParams({ user_id: input.userId });
  if (input.tenantId) params.set("tenant_id", input.tenantId);
  return `${API_BASE_URL}/api/attachments/${encodeURIComponent(attachmentId)}/content?${params.toString()}`;
}
