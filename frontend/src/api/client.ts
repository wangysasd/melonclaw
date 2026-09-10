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
  ServiceStatus,
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

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface ApiRequestOptions {
  method?: "GET" | "POST";
  query?: Record<string, string | number | undefined | null>;
  body?: unknown;
  signal?: AbortSignal;
}

export async function parseErrorResponse(response: Response): Promise<ApiError> {
  let message = `请求失败（${response.status}）。`;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const record = body as Record<string, unknown>;
      const text = record.error ?? record.message;
      if (typeof text === "string" && text.trim()) {
        message = text;
      }
    }
  } catch {
    // 响应体不是 JSON 时保留默认文案。
  }
  return new ApiError(response.status, message);
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
      limit: input.limit ?? 20,
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
