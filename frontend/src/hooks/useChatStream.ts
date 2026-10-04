import { mergeDisplayEvent } from "../lib/modelUsage";
import { mcpChatDisplay } from "../lib/mcpChat";
import type { AssetRef } from "../lib/resultBlocks";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type Dispatch,
} from "react";
import { App as AntdApp } from "antd";

import { ApiError, getConversationHistory } from "../api/client";
import {
  sendApprovalStream,
  sendMessageStream,
  sendUserInputStream,
  type StreamHandlers,
} from "../api/stream";
import type { RunActivity, RunTimings,
  ApprovalDecision,
  AssistantStep,
  AssistantToolCall,
  DisplayEvent,
  MessageStatus,
  MessageModel,
  AttachmentSummary,
  PendingApproval,
  UserInputAnswer,
  UserQuestionRequest,
  StreamEvent,
  Message,
} from "../types/api";
import { isUserQuestionExpired } from "../lib/userQuestionExpiry";
import { useSession } from "../state/session";

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

interface ChatState {
  conversationId: string | null;
  conversationTitle: string | null;
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

type ChatAction =
  | { type: "sync"; state: ChatState }
  | { type: "batch"; actions: ChatAction[] }
  | { type: "reset"; conversationId: string | null }
  | { type: "historyLoading"; conversationId: string | null }
  | { type: "historyPrepended"; conversationId: string; messages: ChatMessage[] }
  | {
      type: "historyLoaded";
      conversationId: string;
      title: string | null;
      projectId: string | null;
      messages: ChatMessage[];
      approval: PendingApproval | null;
      userQuestion: UserQuestionRequest | null;
    }
  | { type: "optimistic"; conversationId: string; projectId: string | null; user: ChatMessage; assistant: ChatMessage }
  | { type: "removeOptimistic"; ids: string[] }
  | {
      type: "messageStarted";
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
    if (messages[index].role === "assistant") {
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
function isTerminalStatus(status: ChatMessage["status"]): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function fromIso(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function historyMessage(item: Message, activeForSnapshot: boolean): ChatMessage {
  let content = item.content;
  if (item.role !== "user" && item.error_code === USER_INPUT_RECOVERY_REQUIRED && !content.trim()) content = RECOVERY_REQUIRED_NOTICE;
  if (!activeForSnapshot && item.role !== "user" && item.status === "pending" && !content.trim()) content = STALE_PENDING_NOTICE;
  return {
    id: item.id, role: item.role === "user" ? "user" : "assistant", content,
    status: item.status, errorCode: item.error_code, timestamp: item.created_at ?? null,
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
    case "historyLoaded":
      return {
        ...state,
        conversationId: action.conversationId,
        conversationTitle: action.title,
        conversationProjectId: action.projectId,
        messages: action.messages,
        approval: action.approval,
        userQuestion: action.userQuestion,
        historyLoading: false,
        restoreDraft: null,
        error: null,
      };
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
            return { ...message, id: action.userMessageId, optimistic: false };
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
        if (message.role !== "assistant" || !["pending", "streaming"].includes(message.status ?? "")) return message;
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

interface SendContext {
  epoch: number;
  conversationId: string;
  userId: string;
  projectId: string;
  modelId: string;
  skillId: string | null;
  draft: string;
  attachmentIds: string[];
  firstMessage?: boolean;
  /** 本条流已消费到的 SSE 序号：只用于丢弃重复投递的帧。 */
  lastEventId?: number;
  onAccepted?: () => void;
}

export interface ChatStreamHandle {
  state: ChatState;
  /** 当前可见会话是否有 AI 输出在跑（左栏转圈与停止键的依据）。 */
  isRunning: boolean;
  /** 正在跑输出的会话 ID（跨会话并发时多项）。 */
  runningConversationIds: string[];
  /** 显式取消当前可见会话的输出：abort 该会话流并乐观置 cancelled。 */
  stopCurrent: () => boolean;
  sendMessage: (
    content: string,
    skillId?: string | null,
    onAccepted?: () => void,
    attachmentIds?: string[],
  ) => Promise<void>;
  submitUserInput: (answer: UserInputAnswer) => Promise<void>;
  submitApproval: (
    decisions:
      | ApprovalDecision[]
      | { interrupt_id: string; decisions: ApprovalDecision[] }[],
  ) => Promise<void>;
  clearRestoreDraft: () => void;
  reloadHistory: () => void;
  loadMessage: (messageId: string, signal: AbortSignal) => Promise<boolean>;
}

export interface UseChatStreamOptions {
  scroll: {
    isNearBottom: () => boolean;
    scrollToBottom: (smooth?: boolean) => void;
  };
}

/** 消费 SSE 聊天流：乐观渲染、事件归约、失败回滚与审批恢复（对齐旧 consumeStream 语义）。 */
export function useChatStream({
  scroll,
}: UseChatStreamOptions): ChatStreamHandle {
  const session = useSession();
  const { message } = AntdApp.useApp();
  const [state, dispatch] = useReducer(reducer, INITIAL_CHAT_STATE);
  const [historyRevision, bumpHistoryRevision] = useReducer((value: number) => value + 1, 0);

  const sessionRef = useRef(session);
  sessionRef.current = session;
  const chatStateRef = useRef(state);
  chatStateRef.current = state;
  /** 多会话并发：按 conversationId 各持一条 SSE 流，切会话不断开。 */
  const activeRunsRef = useRef(new Map<string, { controller: AbortController; context: SendContext }>());
  /** 按会话缓存聊天态：后台会话的事件只写缓存，切回来直接恢复续播。 */
  const chatCacheRef = useRef(new Map<string, ChatState>());
  const historyControllerRef = useRef<AbortController | null>(null);
  const forceHistoryReloadRef = useRef(false);
  const pendingDeltasRef = useRef(new Map<string, ChatAction[]>());
  const deltaFrameRef = useRef<number | null>(null);

  // 可见态同步进缓存：后台事件走缓存，回来时不丢增量。
  useEffect(() => {
    if (state.conversationId) chatCacheRef.current.set(state.conversationId, state);
  }, [state]);

  const matchesContext = useCallback((context: SendContext): boolean => {
    const current = sessionRef.current.getCurrentContext?.() ?? sessionRef.current;
    return (
      context.conversationId === current.conversationId &&
      context.userId === current.userId &&
      context.projectId === current.projectId
    );
  }, []);

  /** 按会话投递归约：永远写缓存；只有可见会话才 dispatch 到当前视图。 */
  const dispatchFor = useCallback((conversationId: string, action: ChatAction): void => {
    const visible = chatStateRef.current.conversationId === conversationId;
    const prev = visible
      ? chatStateRef.current
      : (chatCacheRef.current.get(conversationId) ?? {
          ...INITIAL_CHAT_STATE,
          conversationId,
        });
    const next = reducer(prev, action);
    chatCacheRef.current.set(conversationId, next);
    if (visible) {
      chatStateRef.current = next;
      dispatch({ type: "sync", state: next });
    }
  }, []);

  const flushTextDeltas = useCallback((conversationId?: string): void => {
    const pending = pendingDeltasRef.current;
    const ids = conversationId ? [conversationId] : [...pending.keys()];
    for (const id of ids) {
      const actions = pending.get(id);
      if (!actions?.length) continue;
      pending.delete(id);
      dispatchFor(id, { type: "batch", actions });
    }
  }, [dispatchFor]);

  const queueTextDelta = useCallback((conversationId: string, messageId: string, stepId: string, delta: string, contentKind: "text" | "reasoning" = "text"): void => {
    const pending = pendingDeltasRef.current;
    const actions = pending.get(conversationId) ?? [];
    const previous = actions.at(-1);
    if (previous?.type === "assistantTextDelta" && previous.messageId === messageId && previous.stepId === stepId && previous.contentKind === contentKind) {
      actions[actions.length - 1] = { ...previous, delta: previous.delta + delta };
    } else {
      actions.push({ type: "assistantTextDelta", messageId, stepId, delta, contentKind });
    }
    pending.set(conversationId, actions);
    if (deltaFrameRef.current === null) {
      deltaFrameRef.current = requestAnimationFrame(() => {
        deltaFrameRef.current = null;
        flushTextDeltas();
      });
    }
  }, [flushTextDeltas]);

  useEffect(() => () => {
    if (deltaFrameRef.current !== null) cancelAnimationFrame(deltaFrameRef.current);
    pendingDeltasRef.current.clear();
  }, []);

  /** 除指定会话外是否还有活流：全局 busy 只在所有会话都空闲时才清零。 */
  const othersRunning = useCallback((excludeId: string): boolean => {
    for (const [id, run] of activeRunsRef.current.entries()) {
      if (id !== excludeId && !run.controller.signal.aborted) return true;
    }
    return false;
  }, []);

  const appendStreamText = useCallback((conversationId: string, text: string): boolean => {
    if (!text) return false;
    dispatchFor(conversationId, { type: "text", text });
    return true;
  }, [dispatchFor]);

  const requestHistoryReload = useCallback(() => {
    forceHistoryReloadRef.current = true;
    bumpHistoryRevision();
  }, [bumpHistoryRevision]);

  const reloadHistory = useCallback(() => {
    requestHistoryReload();
  }, [requestHistoryReload]);

  const loadMessage = useCallback(async (messageId: string, signal: AbortSignal): Promise<boolean> => {
    const snapshot = sessionRef.current;
    const conversationId = snapshot.conversationId;
    if (!conversationId) return false;
    if (chatStateRef.current.messages.some((item) => item.id === messageId)) return true;
    const accumulated: ChatMessage[] = [];
    let beforeSeq: number | undefined;
    do {
      const page = await getConversationHistory({ conversationId, userId: snapshot.userId, beforeSeq, limit: 100 }, signal);
      if (signal.aborted || sessionRef.current.epoch !== snapshot.epoch || sessionRef.current.conversationId !== conversationId) return false;
      const active = Boolean(activeRunsRef.current.get(conversationId));
      accumulated.unshift(...page.items.map((item) => historyMessage(item, active)));
      if (page.items.some((item) => item.id === messageId)) {
        dispatchFor(conversationId, { type: "historyPrepended", conversationId, messages: accumulated });
        return true;
      }
      beforeSeq = page.next_before_seq ?? undefined;
    } while (beforeSeq);
    return false;
  }, [dispatchFor]);

  const handleEvent = useCallback(
    (event: StreamEvent, context: SendContext, eventId?: number): void => {
      const conversationId = context.conversationId;
      const viewing = matchesContext(context);
      if (eventId !== undefined) {
        // 服务端帧序号在单条流内单调递增：重复或乱序帧直接丢弃，
        // 不能按文本内容去重（相同文本可能是模型真实重复输出）。
        if (context.lastEventId !== undefined && eventId <= context.lastEventId) return;
        context.lastEventId = eventId;
      }
      if (event.type !== "assistant_text_delta") flushTextDeltas(conversationId);
      const follow = viewing && scroll.isNearBottom();
      switch (event.type) {
        case "run_activity":
          dispatchFor(conversationId, { type: "runActivity", activity: event });
          break;
        case "run_phase":
          dispatchFor(conversationId, { type: "runPhase", phase: event.phase });
          if (viewing) sessionRef.current.setRunStatus(event.phase);
          break;
        case "message_started":
          if (viewing && context.epoch === (sessionRef.current.getCurrentContext?.() ?? sessionRef.current).epoch) context.onAccepted?.();
          context.onAccepted = undefined;
          if (context.firstMessage) {
            sessionRef.current.markConversationStarted(conversationId, context.projectId);
            context.firstMessage = false;
          }
          dispatchFor(conversationId, {
            type: "messageStarted",
            assistantMessageId: event.message_id,
            userMessageId: event.user_message_id,
            resuming: event.resuming,
            model: event.model,
            attachments: event.attachments,
            preparationDurationMs: event.preparation_duration_ms,
          });
          break;
        case "text":
          dispatchFor(conversationId, { type: "runPhase", phase: "responding" });
          if (appendStreamText(conversationId, event.text)) {
            if (viewing) sessionRef.current.setRunStatus("responding");
          }
          break;
        case "assistant_step_started":
          dispatchFor(conversationId, {
            type: "assistantStepStarted",
            messageId: event.message_id,
            step: event.step,
          });
          break;
        case "assistant_text_delta":
          queueTextDelta(conversationId, event.message_id, event.step_id, event.delta, event.content_kind);
          if (viewing) sessionRef.current.setRunStatus(event.content_kind === "reasoning" ? "thinking" : "responding");
          break;
        case "assistant_tool_call":
          dispatchFor(conversationId, {
            type: "assistantToolCall",
            messageId: event.message_id,
            stepId: event.step_id,
            call: event.call,
          });
          if (viewing) sessionRef.current.setRunStatus("processing");
          break;
        case "assistant_tool_result":
          if (event.result.name === "confirm_skill_install" && context.userId === sessionRef.current.userId) {
            void sessionRef.current.refreshSkills();
          }
          dispatchFor(conversationId, {
            type: "assistantToolResult",
            messageId: event.message_id,
            stepId: event.step_id,
            callId: event.call_id,
            result: event.result,
          });
          if (viewing) sessionRef.current.setRunStatus("processing");
          break;
        case "assistant_step_completed":
          dispatchFor(conversationId, {
            type: "assistantStepCompleted",
            messageId: event.message_id,
            stepId: event.step_id,
            content: event.content,
            toolCalls: event.tool_calls,
            contentBlocks: event.content_blocks,
            status: event.status,
          });
          break;
        case "completed":
          sessionRef.current.markConversationIdle?.(conversationId);
          if (viewing) {
            if (!othersRunning(conversationId)) sessionRef.current.setBusy(false);
            sessionRef.current.setRunStatus(null);
          }
          dispatchFor(conversationId, { type: "approvalCleared" });
          dispatchFor(conversationId, {
            type: "completed",
            messageId: event.message_id,
            content: event.content,
            assistantSteps: event.assistant_steps,
            artifacts: event.artifacts,
            executionDurationMs: event.execution_duration_ms,
            timings: event.timings,
          });
          break;
        case "message_status":
          dispatchFor(conversationId, {
            type: "messageStatus",
            messageId: event.message_id,
            status: event.status,
            errorCode: event.error_code,
          });
          sessionRef.current.markConversationIdle?.(conversationId);
          if (viewing) {
            if (event.status === "pending") {
              message.error("该请求正在进行中，请稍候刷新会话。");
              dispatchFor(conversationId, { type: "historyFailed", conversationId, error: "该请求仍在进行中。请稍后重新同步会话，确认结果后再发送新消息。" });
            }
            if (!othersRunning(conversationId)) sessionRef.current.setBusy(false);
            sessionRef.current.setRunStatus(
              event.status === "interrupted"
                ? "waiting"
                : event.status === "failed"
                  ? "failed"
                  : null,
            );
          }
          break;
        case "approval_required":
          if (viewing) {
            sessionRef.current.setBusy(true);
            sessionRef.current.setRunStatus("waiting");
          }
          dispatchFor(conversationId, { type: "runPhase", phase: "waiting" });
          dispatchFor(conversationId, { type: "approvalRequired", request: event.request });
          break;
        case "user_input_required":
          if (viewing) {
            sessionRef.current.setBusy(true);
            sessionRef.current.setRunStatus("waiting");
          }
          dispatchFor(conversationId, { type: "runPhase", phase: "waiting" });
          dispatchFor(conversationId, { type: "userInputRequired", request: event.request });
          break;
        case "user_input_accepted":
          if (viewing) {
            sessionRef.current.setBusy(true);
            sessionRef.current.setRunStatus("processing");
          }
          dispatchFor(conversationId, { type: "userInputAccepted" });
          break;
        case "done":
          if (viewing) {
            if (!othersRunning(conversationId)) sessionRef.current.setBusy(false);
            void sessionRef.current.refreshConversations();
            // 回执型响应（同幂等键重试）背后没有继续执行：如果不回到服务端对账，
            // 用户只会看到卡片消失、然后什么都没发生。
            if (event.terminal_reason === "already_accepted") requestHistoryReload();
          } else {
            void sessionRef.current.refreshConversations();
          }
          break;
        case "error":
          sessionRef.current.markConversationIdle?.(conversationId);
          dispatchFor(conversationId, {
            type: "streamFailed",
            messageId: event.message_id,
            message: context.draft,
            errorCode: event.error_code,
            error: event.message || "助手运行失败。请检查会话状态后重试。",
          });
          if (viewing) {
            if (!othersRunning(conversationId)) sessionRef.current.setBusy(false);
            sessionRef.current.setRunStatus("failed");
            message.error(event.message || "助手运行失败。请重试。");
          }
          break;
        case "model_usage":
        case "context_usage":
          dispatchFor(conversationId, { type: "displayEvent", event: event as DisplayEvent });
          break;
        case "tool_call":
        case "tool_result":
        case "subagent_started":
        case "subagent_text":
        case "subagent_tool_call":
        case "subagent_tool_result":
        case "subagent_completed":
        case "subagent_failed":
          if (viewing) sessionRef.current.setRunStatus("processing");
          dispatchFor(conversationId, { type: "displayEvent", event: event as DisplayEvent });
          break;
        default:
          break;
      }
      if (follow) {
        scroll.scrollToBottom();
      }
    },
    [appendStreamText, dispatchFor, flushTextDeltas, matchesContext, message, othersRunning, queueTextDelta, requestHistoryReload, scroll],
  );

  const runStream = useCallback(
    async (
      stream: (handlers: StreamHandlers) => Promise<void>,
      context: SendContext,
      optimisticIds: string[],
    ): Promise<boolean> => {
      // 同一会话只允许一条流；不同会话可并发，切会话不断开。
      const existing = activeRunsRef.current.get(context.conversationId);
      if (existing && !existing.controller.signal.aborted) return false;
      sessionRef.current.markConversationRunning?.(context.conversationId);
      sessionRef.current.setBusy(true);
      sessionRef.current.setRunStatus("starting");
      let started = false;
      let failed = false;
      let terminal = false;
      const controller = new AbortController();
      activeRunsRef.current.set(context.conversationId, { controller, context });
      sessionRef.current.attachStream(controller, context.conversationId);
      dispatchFor(context.conversationId, { type: "runPhase", phase: "starting" });
      try {
        await stream({
          signal: controller.signal,
          onEvent: (event, eventId) => {
            if (event.type === "message_started") started = true;
            if (event.type === "error") failed = true;
            if (TERMINAL_HINT.has(event.type)) terminal = true;
            handleEvent(event, context, eventId);
          },
        });
        if (!terminal) {
          throw new ApiError(0, "连接意外结束，回复可能不完整。请重新同步会话以确认执行结果。");
        }
        return !failed;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return false;
        }
        const network = error instanceof ApiError && error.status === 0;
        if (!started && !network) {
          dispatchFor(context.conversationId, { type: "removeOptimistic", ids: optimisticIds });
        }
        sessionRef.current.markConversationIdle?.(context.conversationId);
        dispatchFor(context.conversationId, { type: "streamFailed", message: network ? undefined : context.draft, errorCode: network ? "network_disconnected" : error instanceof ApiError ? error.errorCode : undefined, error: error instanceof Error ? error.message : String(error) });
        if (matchesContext(context)) {
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus("failed");
          message.error(error instanceof Error ? error.message : String(error));
        }
        return false;
      } finally {
        flushTextDeltas(context.conversationId);
        if (context.firstMessage) sessionRef.current.cancelConversationSubmission(context.conversationId);
        if (activeRunsRef.current.get(context.conversationId)?.controller === controller) {
          activeRunsRef.current.delete(context.conversationId);
          sessionRef.current.attachStream(null, context.conversationId);
          sessionRef.current.markConversationIdle?.(context.conversationId);
        }
      }
    },
    [dispatchFor, flushTextDeltas, handleEvent, matchesContext, message],
  );

  const sendMessage = useCallback(
    async (
      content: string,
      skillId: string | null = null,
      onAccepted?: () => void,
      attachmentIds: string[] = [],
    ) => {
      const snapshot = sessionRef.current;
      const cleanText = content.trim();
      const displayText = mcpChatDisplay(cleanText);
      const pendingQuestion = chatStateRef.current.userQuestion;
      const canReplaceExpiredQuestion = Boolean(
        pendingQuestion && isUserQuestionExpired(pendingQuestion.expires_at),
      );
      // 并发会话可同时跑：只看当前可见会话是否有流，不看全局 busy。
      const visibleRun = snapshot.conversationId
        ? activeRunsRef.current.get(snapshot.conversationId)
        : undefined;
      const visibleRunning = Boolean(
        visibleRun && !visibleRun.controller.signal.aborted,
      );
      if (
        (!cleanText && attachmentIds.length === 0) ||
        (visibleRunning && !canReplaceExpiredQuestion) ||
        snapshot.conversationCreating
      ) return;
      if (!snapshot.contextReady || snapshot.status?.status !== "ready") {
        message.error("服务仍在准备中，请稍候再发送。");
        return;
      }
      const startUserId = snapshot.userId;
      const startProjectId = snapshot.projectId;
      const startModelId = snapshot.selectedModelId || "";
      const startingConversationId = snapshot.conversationId;
      const localSubmissionId = startingConversationId ? null : `local:${crypto.randomUUID()}`;
      if (localSubmissionId) {
        snapshot.markConversationSubmitted(localSubmissionId, startProjectId, displayText, true);
      }
      let conversationId = startingConversationId;
      if (!conversationId) {
        const conversation = await snapshot.ensureConversation();
        conversationId = conversation?.id ?? null;
      }
      const current = sessionRef.current.getCurrentContext?.() ?? sessionRef.current;
      if (
        !conversationId ||
        conversationId !== current.conversationId ||
        startUserId !== current.userId ||
        startProjectId !== current.projectId
      ) {
        if (localSubmissionId) sessionRef.current.cancelConversationSubmission(localSubmissionId);
        return;
      }
      if (localSubmissionId) {
        sessionRef.current.identifyConversationSubmission(localSubmissionId, conversationId);
      }
      const context: SendContext = {
        epoch: current.epoch,
        conversationId,
        userId: current.userId,
        projectId: current.projectId,
        modelId: startModelId,
        skillId,
        draft: displayText,
        attachmentIds,
        firstMessage: current.draftConversationId === conversationId,
        onAccepted,
      };
      if (!startingConversationId) {
        const reset = { type: "reset" as const, conversationId };
        chatStateRef.current = reducer(chatStateRef.current, reset);
        dispatch(reset);
      }
      const requestId = crypto.randomUUID();
      const optimisticIds = [
        `optimistic-user-${requestId}`,
        `optimistic-assistant-${requestId}`,
      ];
      // 新会话历史与首次发送可能并发；迟到的历史不能覆盖本次乐观消息。
      historyControllerRef.current?.abort();
      dispatchFor(conversationId, {
        type: "optimistic",
        conversationId,
        projectId: context.projectId || null,
        user: {
          id: optimisticIds[0],
          role: "user",
          content: displayText,
          status: null,
          timestamp: new Date().toISOString(),
          markdown: false,
          events: [],
          assistantSteps: [],
          artifacts: [],
          phases: [],
          optimistic: true,
        },
        assistant: {
          id: optimisticIds[1],
          role: "assistant",
          content: "",
          status: "streaming",
          timestamp: null,
          markdown: false,
          events: [],
          assistantSteps: [],
          artifacts: [],
          phases: [],
          // 发送即开始计时：执行区域在 message_started 之前就能显示耗时。
          startedAt: Date.now(),
          optimistic: true,
        },
      });
      if (context.firstMessage && !localSubmissionId) {
        sessionRef.current.markConversationSubmitted(conversationId, context.projectId, displayText);
      }
      scroll.scrollToBottom(true);
      await runStream(
        (handlers) =>
          sendMessageStream(
            conversationId,
            {
              userId: context.userId,
              requestId,
              content: cleanText,
              modelId: context.modelId || null,
              skillId: context.skillId,
              attachmentIds: context.attachmentIds,
            },
            handlers,
          ),
        context,
        optimisticIds,
      );
    },
    [dispatchFor, message, runStream, scroll],
  );

  const submitApproval = useCallback(
    async (
      decisions:
        | ApprovalDecision[]
        | { interrupt_id: string; decisions: ApprovalDecision[] }[],
    ) => {
      const snapshot = sessionRef.current;
      const approval = chatStateRef.current.approval;
      const conversationId = snapshot.conversationId;
      if (!conversationId) return;
      if (!approval?.approval_batch_id || !approval.assistant_message_id) {
        throw new Error(
          "审批提交必须绑定 approval_batch_id 和 assistant_message_id，请刷新后重试。",
        );
      }
      const approvalBatchId = approval.approval_batch_id;
      const assistantMessageId = approval.assistant_message_id;
      const actions = approval.interrupts.flatMap((interrupt) => {
        const grouped = decisions.find((decision) => "interrupt_id" in decision && decision.interrupt_id === interrupt.id);
        const selected = grouped && "decisions" in grouped ? grouped.decisions : decisions as ApprovalDecision[];
        return interrupt.actions.map((action, index) => ({ name: action.name, decision: selected[index]?.type }));
      });
      let accepted = false;
      historyControllerRef.current?.abort();
      const context: SendContext = {
        epoch: snapshot.epoch,
        conversationId,
        userId: snapshot.userId,
        projectId: snapshot.projectId,
        modelId: snapshot.selectedModelId || "",
        skillId: null,
        draft: "",
        attachmentIds: [],
      };
      const succeeded = await runStream(
        (handlers) =>
          sendApprovalStream(
            conversationId,
            {
              userId: context.userId,
              approvalBatchId,
              assistantMessageId,
              decisions,
            },
            {
              ...handlers,
              onEvent: (event, frameId) => {
                const freshFrame = frameId === undefined || context.lastEventId === undefined || frameId > context.lastEventId;
                handlers.onEvent(event, frameId);
                if (freshFrame && !accepted && sessionRef.current.userId === context.userId
                  && activeRunsRef.current.get(conversationId)?.context === context
                  && event.type === "message_started" && event.resuming
                  && event.message_id === assistantMessageId) {
                  accepted = true;
                  dispatchFor(conversationId, { type: "approvalAccepted", messageId: assistantMessageId,
                    receipt: { batchId: approvalBatchId, actions } });
                }
              },
            },
          ),
        context,
        [],
      );
      if (!succeeded && matchesContext(context)) {
        throw new Error(
          "决定未能完成提交或运行已中断。请重新同步会话，确认当前审批状态后再试。",
        );
      }
    },
    [runStream, matchesContext, dispatchFor],
  );

  const submitUserInput = useCallback(
    async (answer: UserInputAnswer) => {
      const snapshot = sessionRef.current;
      const question = chatStateRef.current.userQuestion;
      const conversationId = snapshot.conversationId;
      if (!conversationId || !question) return;
      historyControllerRef.current?.abort();
      const context: SendContext = {
        epoch: snapshot.epoch,
        conversationId,
        userId: snapshot.userId,
        projectId: snapshot.projectId,
        modelId: snapshot.selectedModelId || "",
        skillId: null,
        draft: "",
        attachmentIds: [],
      };
      const succeeded = await runStream(
        (handlers) =>
          sendUserInputStream(
            conversationId,
            {
              userId: context.userId,
              interactionId: question.interaction_id,
              assistantMessageId: question.assistant_message_id,
              decisionRequestId: crypto.randomUUID(),
              answer,
            },
            handlers,
          ),
        context,
        [],
      );
      if (!succeeded && matchesContext(context)) {
        throw new Error("回答未能提交或运行已中断。请重新同步会话后重试。");
      }
    },
    [matchesContext, runStream],
  );

  // 切换会话/用户/项目时加载历史消息（含审批和用户问题恢复）。
  useEffect(() => {
    const snapshot = session;
    const controller = new AbortController();
    historyControllerRef.current = controller;
    const load = async () => {
      const forceReload = forceHistoryReloadRef.current;
      forceHistoryReloadRef.current = false;
      const targetId = snapshot.conversationId;
      if (!targetId) {
        dispatch({ type: "reset", conversationId: null });
        return;
      }
      const hasUnconfirmedDraft = chatStateRef.current.messages.some(
        (item) => item.role === "assistant" && item.errorCode === "network_disconnected",
      );
      if (!forceReload && snapshot.draftConversationId === targetId && !hasUnconfirmedDraft) {
        const cached = chatCacheRef.current.get(targetId);
        if (cached?.messages.length) {
          dispatch({
            type: "historyLoaded",
            conversationId: targetId,
            title: cached.conversationTitle,
            projectId: cached.conversationProjectId,
            messages: cached.messages,
            approval: cached.approval,
            userQuestion: cached.userQuestion,
          });
        } else {
          dispatch({ type: "reset", conversationId: targetId });
        }
        return;
      }
      // 切回仍在跑的会话：直接恢复缓存里的流式增量，不用历史快照覆盖。
      const liveRun = activeRunsRef.current.get(targetId);
      const cached = chatCacheRef.current.get(targetId);
      if (
        !forceReload &&
        liveRun &&
        !liveRun.controller.signal.aborted &&
        cached &&
        cached.messages.length > 0 &&
        cached.conversationId === targetId
      ) {
        dispatch({
          type: "historyLoaded",
          conversationId: targetId,
          title: cached.conversationTitle,
          projectId: cached.conversationProjectId,
          messages: cached.messages,
          approval: cached.approval,
          userQuestion: cached.userQuestion,
        });
        sessionRef.current.setBusy(true);
        sessionRef.current.setRunStatus("processing");
        return;
      }
      const ownRun = activeRunsRef.current.get(targetId);
      if (
        !forceReload &&
        ownRun &&
        !ownRun.controller.signal.aborted &&
        chatStateRef.current.conversationId === targetId
      ) return;
      dispatch({ type: "historyLoading", conversationId: targetId });
      try {
        const data = await getConversationHistory(
          {
            conversationId: targetId,
            userId: snapshot.userId,
            limit: 50,
          },
          controller.signal,
        );
        if (
          controller.signal.aborted ||
          snapshot.conversationId !== sessionRef.current.conversationId ||
          snapshot.epoch !== sessionRef.current.epoch
        ) {
          return;
        }
        const ownActiveRun = activeRunsRef.current.get(targetId);
        const activeStream =
          ownActiveRun && !ownActiveRun.controller.signal.aborted;
        // 同会话的活流：历史只提供已落库内容，后续事件继续由原 SSE 补上。
        const activeForSnapshot = Boolean(activeStream);
        const messages = data.items.map((item) => historyMessage(item, activeForSnapshot));
        const visibleMessages = activeForSnapshot
          ? (() => {
              const liveMessages = chatStateRef.current.conversationId === targetId
                ? chatStateRef.current.messages
                : chatCacheRef.current.get(targetId)?.messages ?? [];
              const merged = messages.map((historyMessage) => {
                const live = liveMessages.find((item) => item.id === historyMessage.id
                  || (item.role === "assistant" && item.optimistic && item.status === "streaming"));
                if (!live || live.role !== "assistant" || isTerminalStatus(live.status)) return historyMessage;
                // 历史提供已落库字段，活流保留尚未落库的文本、步骤与工具事件。
                return {
                  ...historyMessage,
                  ...live,
                  id: historyMessage.id,
                  model: historyMessage.model ?? live.model,
                  attachments: historyMessage.attachments?.length ? historyMessage.attachments : live.attachments,
                };
              });
              const included = new Set(merged.map((item) => item.id));
              return [...merged, ...liveMessages.filter((item) => item.optimistic && !included.has(item.id))];
            })()
          : messages;
        dispatch({
          type: "historyLoaded",
          conversationId: targetId,
          title: data.conversation?.title ?? null,
          projectId: data.conversation?.project_id ?? null,
          messages: visibleMessages,
          approval: data.pending_approval,
          userQuestion: data.pending_interaction ?? null,
        });
        // 首条请求可能已落库，只是开始回执丢失；以历史确认后退出草稿态。
        if (snapshot.draftConversationId === targetId && data.items.length > 0) {
          sessionRef.current.markConversationStarted(targetId, data.conversation?.project_id ?? "");
        }
        if (data.pending_approval || data.pending_interaction) {
          sessionRef.current.setBusy(true);
          sessionRef.current.setRunStatus("waiting");
        } else if (!activeForSnapshot) {
          sessionRef.current.setBusy(false);
          // pending 只是服务端遗留，本地没有活流：按空闲处理，输入框保持可用。
          sessionRef.current.setRunStatus(null);
        } else if (activeForSnapshot) {
          sessionRef.current.setBusy(true);
          sessionRef.current.setRunStatus("processing");
        }
      } catch (error) {
        if (controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
          return;
        }
        const current = sessionRef.current.getCurrentContext?.() ?? sessionRef.current;
        if (snapshot.epoch === current.epoch && snapshot.conversationId === current.conversationId) {
          message.error(error instanceof Error ? error.message : String(error));
          if (snapshot.conversationId) dispatchFor(snapshot.conversationId, {
            type: "historyFailed",
            conversationId: snapshot.conversationId,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
    };
    void load();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.conversationId, session.userId, session.projectId, session.epoch, historyRevision]);

  /**
   * 显式取消某会话的输出：abort 该会话的 SSE 流并乐观置 cancelled。
   * 后端断开后按现有 CancelledError 路径写 cancelled/request_cancelled。
   * 只动该会话，不影响其他并发会话。
   */
  const stopConversation = useCallback((conversationId: string): boolean => {
    if (!conversationId) return false;
    const run = activeRunsRef.current.get(conversationId);
    if (!run || run.controller.signal.aborted) return false;
    flushTextDeltas(conversationId);
    run.controller.abort();
    // abort 后流静默结束：本地先显式收尾，避免卡在 streaming。
    const prev = chatStateRef.current.conversationId === conversationId
      ? chatStateRef.current
      : chatCacheRef.current.get(conversationId);
    const target = prev?.messages.filter(
      (item) => item.role === "assistant" && !isTerminalStatus(item.status),
    ).at(-1);
    if (target) {
      dispatchFor(conversationId, {
        type: "messageStatus",
        messageId: target.id,
        status: "cancelled",
      });
    }
    sessionRef.current.markConversationIdle?.(conversationId);
    if (sessionRef.current.conversationId === conversationId) {
      sessionRef.current.setBusy(false);
      sessionRef.current.setRunStatus(null);
    }
    return true;
  }, [dispatchFor, flushTextDeltas]);

  const stopCurrent = useCallback((): boolean => {
    const currentId = sessionRef.current.conversationId
      ?? chatStateRef.current.conversationId;
    if (!currentId) return false;
    return stopConversation(currentId);
  }, [stopConversation]);

  const runningConversationIds = useMemo(
    () => session.runningConversationIds ?? [],
    [session.runningConversationIds],
  );
  const isRunning = Boolean(
    state.conversationId && runningConversationIds.includes(state.conversationId),
  );

  const handle = useMemo<ChatStreamHandle>(
    () => ({
      state,
      isRunning,
      runningConversationIds,
      stopCurrent,
      sendMessage,
      submitApproval,
      submitUserInput,
      clearRestoreDraft: () => dispatch({ type: "draftRestored" }),
      reloadHistory,
      loadMessage,
    }),
    [state, isRunning, runningConversationIds, stopCurrent, reloadHistory, loadMessage, sendMessage, submitApproval, submitUserInput],
  );

  return handle;
}

const TERMINAL_HINT = new Set([
  "completed",
  "error",
  "approval_required",
  "user_input_required",
  "user_input_accepted",
  "message_status",
]);

export type ChatDispatch = Dispatch<ChatAction>;
