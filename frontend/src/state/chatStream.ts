import { mergeDisplayEvent } from "../lib/modelUsage";
import type { AssetRef } from "../lib/resultBlocks";
import type {
  ApprovalDecision,
  AssistantStep,
  AssistantToolCall,
  AttachmentSummary,
  DisplayEvent,
  Message,
  MessageModel,
  MessageStatus,
  PendingApproval,
  RunActivity,
  RunTimings,
  TaskMessageContext,
  UserQuestionRequest,
} from "../types/api";

/** 与后端 `core/user_input.py` 的同名错误码保持一致。 */
const USER_INPUT_RECOVERY_REQUIRED = "user_input_recovery_required";
const RECOVERY_REQUIRED_NOTICE =
  "这一轮的回答已经收到，但 Agent 没能继续跑完。为了避免把可能带副作用的操作再执行一遍，系统不会自动重放这一轮：请直接发新消息开始新一轮。";
/**
 * 服务端遗留的 pending 轮次（如 Web 进程重启导致执行中断）。
 * 只作为消息内的提示展示：发新消息时后端会自动把这一轮收敛为失败，
 * 不能用全局 error 横幅——那会禁用输入框，反而堵死唯一的解锁出口。
 */
const STALE_PENDING_NOTICE =
  "上一轮请求已中断，未产生回复。直接发送新消息即可继续，系统会自动收尾这一轮。";

/** 执行阶段标签；模型返回的文本由独立 content 流完整展示。 */
export type ReasoningPhase =
  | "starting"
  | "selecting_tools"
  | "thinking"
  | "waiting_model"
  | "preparing_tools"
  | "preparing_file"
  | "responding"
  | "processing"
  | "waiting";

export interface ApprovalReceipt {
  batchId: string;
  actions: { name: string; decision: ApprovalDecision["type"] }[];
}

/** 聊天视图内的消息模型（乐观消息与历史消息统一表示）。 */
export interface ChatMessage {
  taskContext?: TaskMessageContext;
  seq?: number;
  id: string;
  role: "user" | "assistant";
  /** 流式期间为纯文本累加；completed 后整段替换。 */
  content: string;
  /** null = 未知/乐观；"streaming" = 流式进行中。 */
  status: MessageStatus | "streaming" | null;
  timestamp?: string | null;
  errorCode?: string | null;
  /** completed 的助手消息按 Markdown 渲染。 */
  markdown: boolean;
  /** 工具/子代理轨迹事件，与最终回复分层展示。 */
  events: DisplayEvent[];
  /** 根 Agent 的有序 AIMessage steps；正文 content 只保存最终答复。 */
  assistantSteps: AssistantStep[];
  artifacts: AssetRef[];
  /** 当前页面收到服务端恢复确认后的记录；刷新以工具执行历史为准。 */
  approvalReceipts?: ApprovalReceipt[];
  /** 安全的阶段摘要，不保存或展示原始模型思维链。 */
  phases: ReasoningPhase[];
  model?: MessageModel | null;
  executionDurationMs?: number | null;
  /** 运行开始时间（epoch 毫秒）：乐观发送时本地记录，历史加载用 created_at。 */
  timings?: RunTimings;
  startedAt?: number | null;
  /** 本地观测到的终态时间（epoch 毫秒），后端未给 duration 时的兜底。 */
  completedAt?: number | null;
  attachments?: AttachmentSummary[];
  /** 乐观渲染的临时消息（未收到 message_started 前）。 */
  optimistic?: boolean;
}

export interface ChatState {
  conversationId: string | null;
  conversationTitle: string | null;
  conversationTaskId: string | null;
  /** 当前会话自身的项目归属（服务端为准）：附件 hydration 按它取作用域，
   * 不用侧栏当前项目代替，避免跨作用域新建过渡期图片 404。 */
  conversationProjectId: string | null;
  messages: ChatMessage[];
  approval: PendingApproval | null;
  userQuestion: UserQuestionRequest | null;
  historyLoading: boolean;
  /** 失败时需要回填的草稿（仅当输入框为空时生效，对齐旧 restoreDraft）。 */
  restoreDraft: string | null;
  error: string | null;
}

export type ChatAction =
  | { type: "sync"; state: ChatState }
  | { type: "batch"; actions: ChatAction[] }
  | { type: "reset"; conversationId: string | null }
  | { type: "historyLoading"; conversationId: string | null }
  | { type: "taskHistorySynced"; conversationId: string; messages: ChatMessage[] }
  | { type: "taskAssociated"; conversationId: string; taskId: string }
  | { type: "historyPrepended"; conversationId: string; messages: ChatMessage[] }
  | {
      type: "historyLoaded";
      conversationId: string;
      title: string | null;
      taskId?: string | null;
      projectId: string | null;
      messages: ChatMessage[];
      approval: PendingApproval | null;
      userQuestion: UserQuestionRequest | null;
    }
  | { type: "optimistic"; conversationId: string; projectId: string | null; user: ChatMessage; assistant: ChatMessage }
  | { type: "removeOptimistic"; ids: string[] }
  | {
      type: "messageStarted";
      taskContext?: TaskMessageContext;
      assistantMessageId: string;
      userMessageId: string | null;
      resuming?: boolean;
      model?: MessageModel;
      attachments?: AttachmentSummary[];
      preparationDurationMs?: number;
    }
  | { type: "text"; text: string }
  | { type: "assistantStepStarted"; messageId: string; step: AssistantStep }
  | { type: "assistantTextDelta"; messageId: string; stepId: string; delta: string; contentKind?: "text" | "reasoning" }
  | { type: "assistantToolCall"; messageId: string; stepId: string; call: AssistantToolCall }
  | { type: "assistantToolResult"; messageId: string; stepId: string; callId: string; result: AssistantToolCall }
  | { type: "assistantStepCompleted"; messageId: string; stepId: string; content: string; toolCalls: AssistantToolCall[]; status: AssistantStep["status"]; contentBlocks?: AssistantStep["content_blocks"] }
  | { type: "runActivity"; activity: RunActivity }
  | { type: "runPhase"; phase: ReasoningPhase }
  | { type: "displayEvent"; event: DisplayEvent }
  | {
      type: "completed";
      messageId: string;
      content: string;
      assistantSteps: AssistantStep[];
      artifacts: AssetRef[];
      executionDurationMs?: number | null;
      timings?: RunTimings;
    }
  | { type: "messageStatus"; messageId: string; status: MessageStatus; errorCode?: string | null }
  | { type: "streamFailed"; messageId?: string; message?: string; error?: string; errorCode?: string }
  | { type: "historyFailed"; conversationId: string; error: string }
  | { type: "approvalAccepted"; messageId: string; receipt: ApprovalReceipt }
  | { type: "approvalRequired"; request: PendingApproval }
  | { type: "userInputRequired"; request: UserQuestionRequest }
  | { type: "userInputAccepted" }
  | { type: "approvalCleared" }
  | { type: "draftRestored" };

export const INITIAL_CHAT_STATE: ChatState = {
  conversationId: null,
  conversationTitle: null,
  conversationTaskId: null,
  conversationProjectId: null,
  messages: [],
  approval: null,
  userQuestion: null,
  historyLoading: false,
  restoreDraft: null,
  error: null,
};

function upsertLastAssistant(
  state: ChatState,
  update: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const messages = [...state.messages];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === "assistant" && !messages[index].taskContext?.task_run) {
      messages[index] = update(messages[index]);
      break;
    }
  }
  return messages;
}

function updateAssistantById(
  state: ChatState,
  messageId: string,
  update: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  const messages = [...state.messages];
  const index = messages.findIndex(
    (message) => message.role === "assistant" &&
      (message.id === messageId || message.optimistic),
  );
  if (index >= 0) messages[index] = update(messages[index]);
  return messages;
}

function upsertAssistantStep(message: ChatMessage, step: AssistantStep): ChatMessage {
  const steps = [...message.assistantSteps];
  const existing = steps.findIndex((item) => item.id === step.id);
  if (existing >= 0) steps[existing] = { ...steps[existing], ...step };
  else steps.push(step);
  steps.sort((left, right) => left.ordinal - right.ordinal);
  return { ...message, assistantSteps: steps };
}

function updateStep(
  message: ChatMessage,
  stepId: string,
  update: (step: AssistantStep) => AssistantStep,
): ChatMessage {
  const steps = message.assistantSteps.map((step) =>
    step.id === stepId ? update(step) : step,
  );
  return { ...message, assistantSteps: steps };
}

function statusFromToolCalls(
  toolCalls: AssistantToolCall[],
  fallback: AssistantStep["status"],
): AssistantStep["status"] {
  if (toolCalls.length === 0) return fallback;
  if (toolCalls.some((tool) => tool.status === "running")) return "running";
  if (toolCalls.some((tool) => tool.status === "waiting")) return "waiting";
  if (toolCalls.some((tool) => tool.status === "failed")) return "failed";
  if (toolCalls.some((tool) => tool.status === "unknown")) return "unknown";
  return "completed";
}

function appendPhase(message: ChatMessage, phase: ReasoningPhase): ChatMessage {
  const phases = message.phases ?? [];
  return phases.at(-1) === phase
    ? message
    : { ...message, phases: [...phases.filter((item) => item !== phase), phase] };
}

/** 已经结束的运行不能被迟到的事件改回进行中。 */
export function isTerminalStatus(status: ChatMessage["status"]): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function fromIso(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export function historyMessage(item: Message, activeForSnapshot: boolean): ChatMessage {
  let content = item.content;
  if (item.role !== "user" && item.error_code === USER_INPUT_RECOVERY_REQUIRED && !content.trim()) content = RECOVERY_REQUIRED_NOTICE;
  if (!activeForSnapshot && item.role !== "user" && item.status === "pending" && !item.display_metadata?.task_run && !content.trim()) content = STALE_PENDING_NOTICE;
  return {
    id: item.id, seq: item.seq, role: item.role === "user" ? "user" : "assistant", content,
    status: item.status, errorCode: item.error_code, timestamp: item.created_at ?? null,
    taskContext: item.display_metadata ?? undefined,
    markdown: item.role !== "user", events: item.display_metadata?.events ?? [],
    assistantSteps: item.assistant_steps, artifacts: item.artifacts, phases: [],
    model: item.model ?? null, executionDurationMs: item.execution_duration_ms,
    timings: item.display_metadata?.timings, startedAt: fromIso(item.created_at),
    completedAt: null, attachments: item.attachments ?? [],
  };
}

/** 中断等待交互时把运行中的 step/工具标为 waiting，与终态回显保持一致。 */
function markInteractionWaiting(message: ChatMessage): ChatMessage {
  const steps = message.assistantSteps.map((step) => ({
    ...step,
    status: step.status === "streaming" || step.status === "running"
      ? ("waiting" as const)
      : step.status,
    tool_calls: step.tool_calls.map((tool) =>
      tool.status === "running" ? { ...tool, status: "waiting" as const } : tool,
    ),
  }));
  return { ...message, status: "interrupted", markdown: true, assistantSteps: steps };
}

export function reducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "sync":
      return action.state;
    case "batch":
      return action.actions.reduce(reducer, state);
    case "reset":
      return { ...INITIAL_CHAT_STATE, conversationId: action.conversationId };
    case "historyLoading":
      return {
        ...(state.conversationId === action.conversationId ? state : INITIAL_CHAT_STATE),
        conversationId: action.conversationId,
        historyLoading: true,
      };
    case "taskAssociated":
      return state.conversationId === action.conversationId && state.conversationTaskId !== action.taskId
        ? { ...state, conversationTaskId: action.taskId }
        : state;
    case "historyLoaded":
      return {
        ...state,
        conversationId: action.conversationId,
        conversationTitle: action.title,
        conversationTaskId: action.taskId ?? null,
        conversationProjectId: action.projectId,
        messages: action.messages,
        approval: action.approval,
        userQuestion: action.userQuestion,
        historyLoading: false,
        restoreDraft: null,
        error: null,
      };
    case "taskHistorySynced": {
      if (state.conversationId !== action.conversationId) return state;
      const byId = new Map(state.messages.map(item => [item.id, item]));
      for (const fresh of action.messages) {
        const live = byId.get(fresh.id);
        if (live && live.content === fresh.content && live.status === fresh.status && live.seq === fresh.seq
          && live.errorCode === fresh.errorCode && JSON.stringify(live.taskContext) === JSON.stringify(fresh.taskContext)
          && JSON.stringify(live.assistantSteps) === JSON.stringify(fresh.assistantSteps)
          && JSON.stringify(live.events) === JSON.stringify(fresh.events)) continue;
        byId.set(fresh.id, live && !fresh.taskContext?.task_run && (live.status === "streaming" || live.optimistic)
          ? { ...live, seq: fresh.seq } : fresh);
      }
      return { ...state, messages: [...byId.values()].sort((a, b) => (a.seq ?? Infinity) - (b.seq ?? Infinity)) };
    }
    case "historyPrepended": {
      if (state.conversationId !== action.conversationId) return state;
      const existing = new Set(state.messages.map((item) => item.id));
      return { ...state, messages: [...action.messages.filter((item) => !existing.has(item.id)), ...state.messages] };
    }
    case "optimistic":
      return {
        ...state,
        conversationId: action.conversationId,
        conversationProjectId: action.projectId,
        approval: null,
        userQuestion: null,
        historyLoading: false,
        error: null,
        messages: [...state.messages, action.user, action.assistant],
      };
    case "removeOptimistic":
      return {
        ...state,
        messages: state.messages.filter(
          (message) => !action.ids.includes(message.id),
        ),
      };
    case "messageStarted":
      return {
        ...state,
        approval: action.resuming ? null : state.approval,
        userQuestion: action.resuming ? null : state.userQuestion,
        error: null,
        messages: state.messages.map((message) => {
          if (message.role === "assistant" && (message.optimistic || message.id === action.assistantMessageId)) {
            // 迟到的开始事件不能把已经结束的运行重新变成 running。
            if (!message.optimistic && isTerminalStatus(message.status)) return message;
            return {
              ...message,
              id: action.assistantMessageId,
              status: "streaming",
              model: action.model ?? message.model,
              attachments: action.attachments ?? message.attachments,
              assistantSteps: message.assistantSteps,
              startedAt: action.resuming ? Date.now() - (message.executionDurationMs ?? 0) : message.startedAt ?? Date.now(),
              timings: { preparation_ms: (action.resuming ? message.timings?.preparation_ms ?? 0 : 0) +
                (action.preparationDurationMs ?? 0), activities: action.resuming ? message.timings?.activities ?? [] : [] },
              completedAt: null,
              optimistic: false,
            };
          }
          if (
            message.role === "user" &&
            message.optimistic &&
            action.userMessageId
          ) {
            return { ...message, id: action.userMessageId, taskContext: action.taskContext, optimistic: false };
          }
          return message;
        }),
      };
    case "text":
      return {
        ...state,
        messages: upsertLastAssistant(state, (message) =>
          isTerminalStatus(message.status)
            ? message
            : {
                ...appendPhase(message, "responding"),
                content: message.content + action.text,
                status: message.status === "streaming" ? message.status : "streaming",
              },
        ),
      };
    case "assistantStepStarted":
      return {
        ...state,
        messages: updateAssistantById(state, action.messageId, (message) => {
          const known = message.assistantSteps.some((step) => step.id === action.step.id);
          // 已结束的运行只接受已知步骤的更新，避免迟到事件凭空长出新的执行步骤。
          if (isTerminalStatus(message.status) && !known) return message;
          return upsertAssistantStep(message, action.step);
        }),
      };
    case "assistantTextDelta":
      return {
        ...state,
        messages: updateAssistantById(state, action.messageId, (message) =>
          updateStep(appendPhase(message, action.contentKind === "reasoning" ? "thinking" : "responding"), action.stepId, (step) => {
            // 重放会把整段快照当成一次 delta 投递：内容一致时不重复追加。
            if (step.status === "completed" && step.content === action.delta) return step;
            const kind = action.contentKind ?? "text";
            let blocks = step.content_blocks?.map((block) => ({ ...block }));
            if (kind === "reasoning" && !blocks) blocks = step.content ? [{ type: "text", text: step.content }] : [];
            if (blocks) {
              const last = blocks.at(-1);
              if (last?.type === kind) last.text += action.delta;
              else blocks.push({ type: kind, text: action.delta });
            }
            return {
              ...step,
              content_blocks: blocks,
              content: `${step.content}${action.delta}`,
              status: step.status === "completed" ? step.status : "streaming",
            };
          }),
        ),
      };
    case "assistantToolCall":
      return {
        ...state,
        messages: updateAssistantById(state, action.messageId, (message) =>
          updateStep(appendPhase(message, "processing"), action.stepId, (step) => {
            const toolCalls = [...step.tool_calls];
            const index = toolCalls.findIndex((tool) => tool.call_id === action.call.call_id);
            const call = { ...action.call, received_at: toolCalls[index]?.received_at ??
              (action.call.status === "running" ? Date.now() : undefined) };
            if (index >= 0) toolCalls[index] = { ...toolCalls[index], ...call };
            else toolCalls.push(call);
            toolCalls.sort((left, right) => left.batch_index - right.batch_index);
            return { ...step, tool_calls: toolCalls, status: "running" };
          }),
        ),
      };
    case "assistantToolResult":
      return {
        ...state,
        messages: updateAssistantById(state, action.messageId, (message) =>
          updateStep(appendPhase(message, "processing"), action.stepId, (step) => {
            const toolCalls = step.tool_calls.map((tool) =>
              tool.call_id === action.callId ? { ...tool, ...action.result } : tool,
            );
            return {
              ...step,
              tool_calls: toolCalls,
              status: statusFromToolCalls(toolCalls, step.status),
            };
          }),
        ),
      };
    case "assistantStepCompleted":
      return {
        ...state,
        messages: updateAssistantById(state, action.messageId, (message) =>
          updateStep(message, action.stepId, (step) => ({
            ...step,
            content: action.content,
            tool_calls: action.toolCalls.map((tool) => ({ ...tool,
              received_at: step.tool_calls.find((item) => item.call_id === tool.call_id)?.received_at })),
            content_blocks: action.contentBlocks,
            status: statusFromToolCalls(action.toolCalls, action.status),
          })),
        ),
      };
    case "runActivity":
      return { ...state, messages: state.messages.map((message) => {
        if (message.role !== "assistant" || message.taskContext?.task_run || !["pending", "streaming"].includes(message.status ?? "")) return message;
        const activities = [...(message.timings?.activities ?? [])];
        const index = activities.findIndex((item) => item.id === action.activity.id);
        if (index >= 0) activities[index] = { ...activities[index], ...action.activity };
        else if (activities.length < 128) activities.push({ ...action.activity, receivedAt: Date.now() });
        return { ...message, timings: { preparation_ms: message.timings?.preparation_ms ?? 0, activities } };
      }) };
    case "runPhase":
      return {
        ...state,
        messages: upsertLastAssistant(state, (message) => appendPhase(message, action.phase)),
      };
    case "displayEvent":
      return {
        ...state,
        messages: upsertLastAssistant(state, (message) => ({
          ...(["model_usage", "context_usage"].includes(action.event.type) ? message : appendPhase(message, "processing")),
          events: mergeDisplayEvent(message.events, action.event),
        })),
      };
    case "completed":
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.role === "assistant" &&
          (message.id === action.messageId || message.optimistic)
            ? {
                ...message,
                id: action.messageId,
                content: action.content,
                timings: action.timings ?? message.timings,
                executionDurationMs: action.executionDurationMs,
                status: "completed" as const,
                markdown: true,
                completedAt: message.completedAt ?? Date.now(),
                // completed 携带服务端已落库的完整快照，终态只认这一份权威数据。
                assistantSteps: action.assistantSteps,
                artifacts: action.artifacts,
                optimistic: false,
              }
            : message,
        ),
      };
    case "messageStatus":
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.role === "assistant" &&
          (message.id === action.messageId || message.optimistic)
            ? {
                ...message,
                id: action.messageId,
                status: action.status,
                errorCode: action.errorCode,
                completedAt: isTerminalStatus(action.status)
                  ? message.completedAt ?? Date.now()
                  : message.completedAt ?? null,
                optimistic: false,
              }
            : message,
        ),
      };
    case "streamFailed":
      return {
        ...state,
        messages: state.messages.map((message) => message.role === "assistant" &&
          (action.messageId ? message.id === action.messageId : message.status === "streaming") ? ({
          ...message,
          status: action.errorCode === "network_disconnected" ? "pending" : "failed",
          errorCode: action.errorCode,
          markdown: true,
          completedAt: message.completedAt ?? Date.now(),
        }) : message),
        restoreDraft: state.restoreDraft ?? action.message ?? null,
        error: action.error ?? state.error,
      };
    case "historyFailed":
      return state.conversationId === action.conversationId
        ? { ...state, historyLoading: false, error: action.error }
        : state;
    case "approvalAccepted":
      return { ...state, messages: state.messages.map((message) => {
        if (message.id !== action.messageId || message.role !== "assistant") return message;
        const receipts = message.approvalReceipts ?? [];
        if (receipts.some((receipt) => receipt.batchId === action.receipt.batchId)) return message;
        return { ...message, approvalReceipts: [...receipts, action.receipt] };
      }) };
    case "approvalRequired":
      return { ...state, approval: action.request, messages: upsertLastAssistant(state, (message) => markInteractionWaiting(appendPhase(message, "waiting"))) };
    case "userInputRequired":
      return { ...state, userQuestion: action.request, approval: null, messages: upsertLastAssistant(state, (message) => markInteractionWaiting(appendPhase(message, "waiting"))) };
    case "userInputAccepted":
      return { ...state, userQuestion: null };
    case "approvalCleared":
      return { ...state, approval: null, userQuestion: null };
    case "draftRestored":
      return { ...state, restoreDraft: null };
    default:
      return state;
  }
}
