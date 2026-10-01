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

/** 系统内置模型 + 用户在插件管理中自建的自定义模型共用同一契约。 */
export interface ModelOption {
  id: string;
  display_name: string;
  source: "system" | "custom";
  provider: string;
  /** 归属供应商标识，用于匹配 logo（`providerIcons.ts`）。 */
  provider_key: string;
  model: string;
  available: boolean;
  is_default: boolean;
  /** 模型归属：global=全员共享，user=当前用户私有。 */
  scope?: "global" | "user";
  config_version?: number;
  input_modalities?: string[];
}

export interface ModelCatalog {
  items: ModelOption[];
  default_model_id: string;
}

/** GET /api/skills 返回的 Skill 摘要；正文仍只由 Agent 通过虚拟路径读取。 */
export interface SkillOption {
  id: string;
  display_name: string;
  description: string;
  /** global=全员共享（管理员发布），user=当前用户私有。 */
  scope: "global" | "user";
}

/** POST /api/skills/import/prepare 返回的两段式安装草稿预览。 */
export interface SkillImportDraft {
  draft_id: string;
  name: string;
  display_name: string;
  description: string;
  file_count: number;
  expires_at: number;
  scope: "global" | "user";
  operation: "install" | "update";
  target_id: string | null;
  base_version: number | null;
  source_type: string;
  source_url: string;
  source_ref: string;
  preview: SkillContentPreview;
}

export interface SkillContentPreview {
  content_hash: string;
  body: string;
  body_truncated: boolean;
  files: { path: string; size: number; sha256: string }[];
  changes: { added: string[]; removed: string[]; modified: string[] };
  diff: string;
  dependency_checks: { kind: string; name: string; status: "available" | "missing" | "manual" }[];
}

/** MCP 列表不回显凭据，普通用户的全局项不包含连接字段。 */
export interface McpServer {
  id: string;
  slug: string;
  display_name: string;
  description: string;
  scope: "global" | "user";
  transport: "http" | "sse" | "stdio";
  version: number;
  enabled: boolean;
  personally_enabled: boolean;
  effective_enabled: boolean;
  shadowed: boolean;
  shadows_global: boolean;
  unavailable_reason: string | null;
  can_edit: boolean;
  can_delete: boolean;
  can_test: boolean;
  url?: string | null;
  command?: string | null;
  args?: string[];
  headers_keys?: string[];
  env_keys?: string[];
  tool_allowlist?: string[] | null;
}

export interface McpCredentialPatch {
  set: Record<string, string>;
  remove: string[];
  clear: boolean;
}

export interface McpConfiguration {
  slug: string;
  display_name: string;
  description: string;
  transport: "http" | "sse" | "stdio";
  url: string | null;
  command: string | null;
  args: string[];
  headers: McpCredentialPatch;
  env: McpCredentialPatch;
  tool_allowlist: string[] | null;
}

export interface McpTestResult {
  ok: boolean; tool_count: number; tool_names: string[]; message: string;
  error_code: "busy" | "timeout" | "authentication" | "connection" | null;
  tool_details: { name: string; description: string }[];
}

export interface McpToolDiscoveryResult {
  ok: boolean;
  tool_count: number;
  enabled_tool_count: number;
  tools: { name: string; description: string; enabled: boolean }[];
  missing_allowed_tools: string[];
  error_code: McpTestResult["error_code"];
  message: string;
}

/** GET /api/skills/manage 返回的可展示 Skill（不含全员停用的共享项）。 */
export interface ManageableSkill {
  id: string;
  selection_id: string;
  version: number;
  content_hash: string;
  source_url: string;
  source_ref: string;
  personally_enabled: boolean;
  effective_enabled: boolean;
  unavailable_reason: string | null;
  diagnostic: string;
  name: string;
  scope: "global" | "user";
  source_type: string;
  /** 共享项的全员开关（仅管理员可改）；私有项即创建者的启停。 */
  enabled: boolean;
  /** 共享项的个人启停偏好（null = 默认启用）；私有项恒为 null。 */
  user_enabled: boolean | null;
  created_by: string;
  display_name: string;
  description: string;
  /**
   * 磁盘状态。"missing" = 目录已丢失（只能删）；"invalid" = 目录还在但读不出
   * 合法 SKILL.md（修好文件就能用）；两者都不会出现在选择器里。
   */
  availability: "ready" | "missing" | "invalid" | "pending";
  /** 共享项专属：true 表示被当前用户自己的同名私有 Skill 遮蔽，
   * 不会出现在该用户的 Agent 目录里；删除/改名私有 Skill 后恢复。 */
  shadowed?: boolean;
}

/** GET /api/model-providers 返回的可管理模型供应商；api_key 绝不回显。 */
export interface ManageableProvider {
  provider_key: string;
  scope: "global";
  /** system=平台种子供应商（不可删除），manual=管理员新建。 */
  source_type: string;
  display_name: string;
  provider_type: string;
  api_key_env: string;
  has_request_headers: boolean;
  extra_config: Record<string, unknown>;
  base_url: string;
  /** 远端模型列表端点；null = 不支持拉取。 */
  models_endpoint: string | null;
  /** admin 配的共享 Key 有无（只回显有无，不回显值）。 */
  has_api_key: boolean;
  /** 当前用户覆盖的 Key 有无。 */
  has_my_key: boolean;
  /** 两者任一有即 True，前端据此判断可用性。 */
  effective_has_key: boolean;
  /** 该供应商下已启用模型数（卡片 footer 用）。 */
  enabled_models_count: number;
  enabled: boolean;
  created_by: string;
}

/** GET /api/models/manage 返回的可管理自定义模型；连接与凭据归供应商。 */
export interface ManageableModel {
  model_key: string;
  /** 归属的模型供应商。 */
  provider_key: string;
  /** 供应商展示名（联查得出）。 */
  provider_display_name: string;
  scope: "global" | "user";
  /** 创建来源；当前模型均由用户手动配置（manual）。 */
  source_type: string;
  display_name: string;
  model_name: string;
  enabled: boolean;
  /** 是否为默认模型（全局至多一个，每个用户的个人模型至多一个）。 */
  is_default: boolean;
  input_modalities: string[];
  created_by: string;
}

/* ---------- 开发用户 ---------- */

/** GET /api/dev/users 响应项（开发模拟用户，非生产认证）。 */
export interface DevUser {
  user_id: string;
  display_name: string;
  user_name_zh: string;
  username: string;
  is_default: boolean;
  tenant_id: string;
  tenant_role: string;
  tenant_status: string;
}

/* ---------- 项目与会话 ---------- */

export interface Project {
  id: string;
  name: string;
  is_pinned?: boolean;
  workdir_path?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ConversationSummary {
  id: string;
  project_id: string | null;
  is_pinned?: boolean;
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

export type AssistantStepStatus =
  | "streaming"
  | "running"
  | "completed"
  | "failed"
  | "waiting"
  | "unknown";

export type AssistantToolStatus =
  | "running"
  | "completed"
  | "failed"
  | "waiting"
  | "unknown";

export interface AssistantToolCall {
  call_id: string;
  name: string;
  batch_index: number;
  args_preview?: string;
  result_preview?: string;
  status: AssistantToolStatus;
  error?: string;
  /** epoch 毫秒；服务端只在真实观测到调用/结果时写入，缺失表示没有可靠耗时。 */
  started_at?: number;
  completed_at?: number;
}

export interface AssistantStep {
  id: string;
  ordinal: number;
  source_message_id?: string;
  content: string;
  status: AssistantStepStatus;
  is_final: boolean;
  tool_calls: AssistantToolCall[];
  truncated?: boolean;
}

/** 历史重放的子代理轨迹事件，结构同 SSE 事件（见 StreamEvent）。 */
export interface DisplayEvent {
  type: string;
  [key: string]: unknown;
}

export interface Message {
  id: string;
  role: string;
  status: MessageStatus;
  content: string;
  /** 新协议始终返回完整的助手步骤快照；空数组表示本轮没有过程步骤。 */
  assistant_steps: AssistantStep[];
  execution_duration_ms?: number | null;
  created_at?: string;
  error_code?: string | null;
  model?: MessageModel | null;
  display_metadata?: {
    events?: DisplayEvent[];
    skill?: { id: string; display_name: string };
    capabilities?: string[];
  } | null;
  attachments?: AttachmentSummary[];
}

export interface AttachmentSummary {
  attachment_id: string;
  file_name: string;
  media_type: string;
  kind: "image" | "pdf" | "text" | "document" | "archive";
  size_bytes: number;
  parse_status: "not_required" | "pending" | "processing" | "processed" | "failed";
  parse_error_code?: string | null;
  status?: "staged" | "attached" | "expired" | "deleted";
  sha256?: string;
  expires_at?: string | null;
}

/** GET /api/attachments/capabilities 中的单个类型。 */
export interface AttachmentTypeSpec {
  extension: string;
  media_type: string;
  kind: AttachmentSummary["kind"];
}

/**
 * GET /api/attachments/capabilities 响应：附件类型与限制的唯一来源。
 * 前端只做上传前预校验，服务端仍会重新校验。
 */
export interface AttachmentCapabilities {
  items: AttachmentTypeSpec[];
  max_file_bytes: number;
  max_total_bytes: number;
  max_per_message: number;
  workspace_max_bytes: number;
  image_max_pixels: number;
  pdf_max_pages: number;
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
  pending_interaction?: UserQuestionRequest | null;
}

/* ---------- HITL 审批 ---------- */

export type DecisionType = "approve" | "reject" | "respond" | "edit";

export interface ApprovalAction {
  name: string;
  /** 后端返回经过脱敏的 JSON 文本。 */
  args: string;
  description: string;
  allowed_decisions: DecisionType[];
}

export interface ApprovalInterrupt {
  id: string;
  actions: ApprovalAction[];
}

/**
 * pending_approval / approval_required 事件中的审批批次。
 */
export interface PendingApproval {
  approval_batch_id: string;
  assistant_message_id: string;
  interrupts: ApprovalInterrupt[];
}

export interface UserQuestionOption {
  id: string;
  label: string;
  description?: string | null;
}

export interface UserQuestionItem {
  id: string;
  question: string;
  options: UserQuestionOption[];
  allow_custom_answer: boolean;
  multi_select: boolean;
}

export interface UserQuestionRequest {
  kind: "user_question";
  schema_version: 2;
  interaction_id: string;
  interrupt_id: string;
  assistant_message_id: string;
  /** 一张卡片里的全部独立问题；单题就是长度为一的数组。 */
  questions: UserQuestionItem[];
  expires_at: string;
}

export type SingleUserInputAnswer =
  | { type: "option"; option_id: string }
  | { type: "options"; option_ids: string[]; text?: string }
  | { type: "text"; text: string }
  | { type: "cancelled" };

/* cancelled 表示用户跳过：Agent 会被唤醒并自行决定后续步骤。 */
export type UserInputAnswer =
  | SingleUserInputAnswer
  | { type: "batch"; answers: Record<string, SingleUserInputAnswer> }
  | { type: "cancelled" };

export interface ApprovalDecision {
  type: DecisionType;
  message?: string;
  edited_action?: { name: string; args: Record<string, unknown> };
}

/* ---------- 请求负载 ---------- */

export interface CreateProjectInput {
  userId: string;
  name: string;
}

export interface ListConversationsInput {
  userId: string;
  projectId?: string | null;
  scope?: "unassigned";
  limit?: number;
  cursor?: string | null;
}

export interface CreateConversationInput {
  userId: string;
  projectId?: string | null;
}

export interface ListMessagesInput {
  conversationId: string;
  userId: string;
  limit?: number;
  beforeSeq?: number | null;
}

export interface SendMessageInput {
  userId: string;
  /** 每次发送生成新的 UUID，用于服务端幂等。 */
  requestId: string;
  content: string;
  attachmentIds?: string[];
  modelId?: string | null;
  skillId?: string | null;
}

export interface SendApprovalInput {
  userId: string;
  approvalBatchId: string;
  assistantMessageId: string;
  decisions: ApprovalDecision[] | { interrupt_id: string; decisions: ApprovalDecision[] }[];
}

export interface SendUserInputInput {
  userId: string;
  interactionId: string;
  assistantMessageId: string;
  decisionRequestId: string;
  answer: UserInputAnswer;
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
      attachments?: AttachmentSummary[];
    }
  | { type: "text"; text: string }
  | {
      type: "assistant_step_started";
      message_id: string;
      step: AssistantStep;
    }
  | {
      type: "assistant_text_delta";
      message_id: string;
      step_id: string;
      delta: string;
    }
  | {
      type: "assistant_tool_call";
      message_id: string;
      step_id: string;
      call: AssistantToolCall;
    }
  | {
      type: "assistant_tool_result";
      message_id: string;
      step_id: string;
      call_id: string;
      result: AssistantToolCall;
    }
  | {
      type: "assistant_step_completed";
      message_id: string;
      step_id: string;
      content: string;
      tool_calls: AssistantToolCall[];
      status: AssistantStepStatus;
    }
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
  | { type: "user_input_required"; request: UserQuestionRequest }
  | {
      type: "user_input_accepted";
      interaction_id: string;
      decision_request_id: string;
      assistant_message_id: string;
    }
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
      /** 终态权威快照；前端不再从局部增量推断最终步骤。 */
      assistant_steps: AssistantStep[];
      execution_duration_ms?: number | null;
      replayed?: boolean;
    }
  | { type: "done"; message_id?: string; terminal_reason?: string; replayed?: boolean }
  | { type: "error"; message?: string; message_id?: string };

/**
 * 注意：不要加 { type: string } 兜底成员，会破坏判别联合的窄化；
 * 后端新增事件类型时消费方 switch default 分支自然忽略。
 */
