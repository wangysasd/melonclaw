/**
 * MelonClaw Web API 类型契约。
 *
 * 依据 src/melonclaw/web/app.py 的请求模型与 React API 消费逻辑整理，
 * 是前后端之间的稳定契约；后端字段变更时需同步更新本文件。
 */

/* ---------- 服务状态 ---------- */

export type ServiceState = "starting" | "ready" | "error";

/** GET /api/status 响应。503 未就绪时后端也会返回该结构。 */
export interface ServiceStatus {
  status: ServiceState;
  /** error 状态下的脱敏错误信息；其余状态为空串。 */
  message: string;
  provider: string;
  model: string;
  mcp_servers: string[];
  skills: string[];
  database: string;
  memory_store: string;
}

/* ---------- 模型目录 ---------- */

/** 第一阶段只返回代码定义的系统模型；后续用户模型沿用同一契约。 */
export interface ModelOption {
  id: string;
  display_name: string;
  source: "system";
  provider: string;
  model: string;
  available: boolean;
  is_default: boolean;
  config_version?: number;
}

export interface ModelCatalog {
  items: ModelOption[];
  default_model_id: string;
}

/* ---------- 开发用户 ---------- */

/** GET /api/dev/users 响应项（开发模拟用户，非生产认证）。 */
export interface DevUser {
  user_id: string;
  display_name: string;
  user_name_zh: string;
  username: string;
  is_default: boolean;
  tenant_ids: string[];
  tenant_id: string | null;
  default_tenant_id: string | null;
}

/* ---------- 项目与会话 ---------- */

export interface Project {
  id: string;
  name: string;
  is_default?: boolean;
  workdir_path?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationSummary {
  id: string;
  project_id?: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
}

/* ---------- 消息 ---------- */

export type MessageStatus =
  | "pending"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

/** 历史重放的工具/子代理轨迹事件，结构同 SSE 事件（见 StreamEvent）。 */
export interface DisplayEvent {
  type: string;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  role: string;
  status: MessageStatus;
  content: string;
  created_at?: string;
  error_code?: string | null;
  model?: MessageModel | null;
  display_metadata?: { events?: DisplayEvent[] } | null;
}

/** 消息历史和 SSE 中使用的非敏感模型快照。 */
export interface MessageModel {
  id: string;
  display_name: string;
  provider: string;
  model: string;
  config_version?: number;
}

/** GET /api/conversations/{id}/messages 响应。 */
export interface ConversationHistory {
  conversation: ConversationSummary;
  items: Message[];
  next_before_seq?: number | null;
  pending_approval: PendingApproval | null;
}

/* ---------- HITL 审批 ---------- */

export type DecisionType = "approve" | "reject" | "respond" | "edit";

export interface ApprovalAction {
  name: string;
  /** 后端返回经过脱敏的 JSON 文本；兼容对象形式。 */
  args?: string | Record<string, unknown>;
  description?: string | null;
  allowed_decisions?: DecisionType[];
}

export interface ApprovalInterrupt {
  id: string;
  actions: ApprovalAction[];
}

/**
 * pending_approval / approval_required 事件中的审批请求。
 * 单 interrupt 时为顶层 actions；多 interrupt 时为 interrupts 数组。
 */
export interface PendingApproval {
  id?: string;
  actions?: ApprovalAction[];
  interrupts?: ApprovalInterrupt[];
}

export interface ApprovalDecision {
  type: DecisionType;
  message?: string;
  edited_action?: { name: string; args: Record<string, unknown> };
}

/* ---------- 请求负载 ---------- */

export interface CreateProjectInput {
  userId: string;
  tenantId?: string | null;
  name: string;
}

export interface ListConversationsInput {
  userId: string;
  tenantId?: string | null;
  projectId?: string | null;
  limit?: number;
  cursor?: string | null;
}

export interface CreateConversationInput {
  userId: string;
  tenantId?: string | null;
  projectId?: string | null;
}

export interface ListMessagesInput {
  conversationId: string;
  userId: string;
  tenantId?: string | null;
  limit?: number;
  beforeSeq?: number | null;
}

export interface SendMessageInput {
  userId: string;
  tenantId?: string | null;
  /** 每次发送生成新的 UUID，用于服务端幂等。 */
  requestId: string;
  content: string;
  modelId?: string | null;
}

export interface SendApprovalInput {
  userId: string;
  tenantId?: string | null;
  decisions: ApprovalDecision[] | { interrupt_id: string; decisions: ApprovalDecision[] }[];
}

/* ---------- SSE 流事件 ---------- */

export type StreamEvent =
  | { type: "run_phase"; phase: "selecting_tools" | "thinking" }
  | {
      type: "message_started";
      conversation_id: string;
      request_id: string;
      user_message_id: string | null;
      message_id: string;
      resuming?: boolean;
      model?: MessageModel;
    }
  | { type: "text"; text: string }
  | {
      type: "tool_call";
      call_key?: string;
      name: string;
      args?: unknown;
      status?: string;
    }
  | {
      type: "tool_result";
      call_key?: string;
      name?: string;
      status?: string;
      content?: string;
    }
  | {
      type: "subagent_started";
      subagent_id: string;
      subagent_name?: string;
      parent_call_id?: string | null;
      parent_subagent_id?: string | null;
    }
  | { type: "subagent_text"; subagent_id: string; text: string }
  | {
      type: "subagent_tool_call";
      subagent_id: string;
      call_key?: string;
      name: string;
      args?: unknown;
      status?: string;
    }
  | {
      type: "subagent_tool_result";
      subagent_id: string;
      call_key?: string;
      name?: string;
      status?: string;
      content?: string;
    }
  | { type: "subagent_completed"; subagent_id: string; status?: string }
  | { type: "subagent_failed"; subagent_id: string; error?: string; status?: string }
  | { type: "approval_required"; request: PendingApproval }
  | {
      type: "message_status";
      message_id: string;
      request_id?: string;
      status: MessageStatus;
      error_code?: string | null;
      replayed?: boolean;
    }
  | {
      type: "completed";
      message_id: string;
      request_id?: string;
      content: string;
      replayed?: boolean;
    }
  | { type: "done"; message_id?: string; terminal_reason?: string; replayed?: boolean }
  | { type: "error"; message?: string; message_id?: string };

/**
 * 注意：不要加 { type: string } 兜底成员，会破坏判别联合的窄化；
 * 后端新增事件类型时消费方 switch default 分支自然忽略。
 */
