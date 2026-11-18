import { useVisibleRefresh } from "./useVisibleRefresh";
import { environmentChatDisplay } from "../lib/environmentChat";
import { mcpChatDisplay } from "../lib/mcpChat";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";
import { App as AntdApp } from "antd";

import { apiRequest, ApiError, getConversationHistory, retryAttachmentParse } from "../api/client";
import {
  sendApprovalStream,
  sendMessageStream,
  sendUserInputStream,
  type StreamHandlers,
} from "../api/stream";
import type {
  ApprovalDecision,
  AttachmentSummary,
  DisplayEvent,
  Message,
  StreamEvent,
  UserInputAnswer,
} from "../types/api";
import { isUserQuestionExpired } from "../lib/userQuestionExpiry";
import { createRequestId } from "../lib/requestId";
import {
  historyMessage,
  INITIAL_CHAT_STATE,
  isTerminalStatus,
  reducer,
  type ChatAction,
  type ChatMessage,
  type ChatState,
} from "../state/chatStream";
import { useSession } from "../state/session";

interface SendContext {
  epoch: number;
  conversationId: string;
  userId: string;
  projectId: string;
  modelId: string;
  skillId: string | null;
  draft: string;
  attachmentIds: string[];
  knowledgeBaseIds: string[];
  firstMessage?: boolean;
  assistantMessageId?: string;
  queued?: boolean;
  /** 本条流已消费到的 SSE 序号：只用于丢弃重复投递的帧。 */
  lastEventId?: number;
  onAccepted?: () => void;
}

interface QueuedMessage {
  id: string;
  content: string;
  preview: string;
  modelId: string;
  skillId?: string | null;
  attachmentIds: string[];
  imageAttachmentIds: string[];
  knowledgeBaseIds: string[];
}

interface ConversationQueue {
  userId: string;
  projectId: string;
  items: QueuedMessage[];
  blocked?: { reason: string; messageId?: string; attachmentPreparationFailed?: boolean };
  sending?: string;
  checking?: boolean;
  ready?: boolean;
}

export interface ChatStreamHandle {
  queue?: ConversationQueue;
  moveQueued: (id: string, targetId: string) => void;
  removeQueued: (id: string) => void;
  sendQueued: () => Promise<void>;
  state: ChatState;
  /** 当前可见会话是否有 AI 输出在跑（左栏转圈与停止键的依据）。 */
  isRunning: boolean;
  preparingAttachments: boolean;
  /** 正在跑输出的会话 ID（跨会话并发时多项）。 */
  runningConversationIds: string[];
  /** 显式取消当前可见会话的输出：abort 该会话流并乐观置 cancelled。 */
  stopCurrent: () => boolean;
  sendMessage: (
    content: string,
    skillId?: string | null,
    onAccepted?: () => void,
    attachmentIds?: string[],
    knowledgeBaseIds?: string[],
    onSubmitted?: () => void,
    imageAttachmentIds?: string[],
    onAttachmentsPrepared?: (attachments: AttachmentSummary[]) => void,
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
  active?: boolean;
  scroll: {
    isNearBottom: () => boolean;
    scrollToBottom: (smooth?: boolean) => void;
  };
}

/** 消费 SSE 聊天流：乐观渲染、事件归约、失败回滚与审批恢复（对齐旧 consumeStream 语义）。 */
export function useChatStream({
  scroll, active = true,
}: UseChatStreamOptions): ChatStreamHandle {
  const session = useSession();
  const { message } = AntdApp.useApp();
  const [state, dispatch] = useReducer(reducer, INITIAL_CHAT_STATE);
  const [historyRevision, bumpHistoryRevision] = useReducer((value: number) => value + 1, 0);

  const queuesRef = useRef(new Map<string, ConversationQueue>());
  const [, refreshQueue] = useReducer((version: number) => version + 1, 0);
  const drainQueueRef = useRef<(conversationId: string) => void>(() => {});
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const chatStateRef = useRef(state);
  chatStateRef.current = state;
  /** 多会话并发：按 conversationId 各持一条 SSE 流，切会话不断开。 */
  const activeRunsRef = useRef(new Map<string, { controller: AbortController; context: SendContext }>());
  const attachmentPreparationsRef = useRef(new Set<string>());
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

  const associatedTaskId = session.recents.find(item => item.id === session.conversationId)?.task_id
    ?? session.conversations.find(item => item.id === session.conversationId)?.task_id;
  useEffect(() => {
    // 聊天中创建任务后，列表刷新带回绑定；保留当前消息和活流并开启任务结果同步。
    if (session.conversationId && associatedTaskId && state.conversationId === session.conversationId
      && state.conversationTaskId !== associatedTaskId) {
      dispatchFor(session.conversationId, { type: "taskAssociated", conversationId: session.conversationId, taskId: associatedTaskId });
    }
  }, [associatedTaskId, session.conversationId, state.conversationId, state.conversationTaskId, dispatchFor]);

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
          if (context.queued || (viewing && context.epoch === (sessionRef.current.getCurrentContext?.() ?? sessionRef.current).epoch)) context.onAccepted?.();
          context.onAccepted = undefined;
          if (context.firstMessage) {
            sessionRef.current.markConversationStarted(conversationId, context.projectId);
            context.firstMessage = false;
          }
          dispatchFor(conversationId, {
            type: "messageStarted",
            taskContext: event.task_context,
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
          if (["confirm_skill_install", "confirm_skill_batch"].includes(event.result.name) && context.userId === sessionRef.current.userId) {
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
      if (matchesContext(context)) {
        sessionRef.current.setBusy(true);
        sessionRef.current.setRunStatus("starting");
      }
      let started = false;
      let failed = false;
      let terminal = false;
      let completed = false;
      let waiting = false;
      let assistantId = context.assistantMessageId;
      let reason = "上一轮未正常完成";
      const controller = new AbortController();
      activeRunsRef.current.set(context.conversationId, { controller, context });
      sessionRef.current.attachStream(controller, context.conversationId);
      dispatchFor(context.conversationId, { type: "runPhase", phase: "starting" });
      try {
        await stream({
          signal: controller.signal,
          onEvent: (event, eventId) => {
            if (eventId !== undefined && context.lastEventId !== undefined && eventId <= context.lastEventId) return;
            if (event.type === "message_started") {
              started = true;
              assistantId = event.message_id;
            }
            if (event.type === "message_status") assistantId = event.message_id;
            if (event.type === "completed") completed = true;
            if (event.type === "approval_required" || event.type === "user_input_required") waiting = true;
            if (event.type === "error" || (event.type === "done" && event.terminal_reason === "transport_error")) failed = true;
            if (TERMINAL_HINT.has(event.type)) terminal = true;
            handleEvent(event, context, eventId);
          },
        });
        if (!terminal) {
          throw new ApiError(0, "连接意外结束，回复可能不完整。请重新同步会话以确认执行结果。");
        }
        return !failed;
      } catch (error) {
        failed = true;
        reason = "发送失败或连接已断开";
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
          const queue = queuesRef.current.get(context.conversationId);
          if (queue && queue.userId === context.userId && (!waiting || failed || controller.signal.aborted)) {
            if (!completed || failed || controller.signal.aborted) {
              queue.blocked = {
                reason: controller.signal.aborted ? "已停止接收回复" : reason,
                messageId: !context.queued || started ? assistantId : undefined,
              };
              refreshQueue();
            } else {
              queue.ready = true;
              queueMicrotask(() => drainQueueRef.current(context.conversationId));
            }
          }
        }
      }
    },
    [dispatchFor, flushTextDeltas, handleEvent, matchesContext, message],
  );

  const sendNow = useCallback(
    async (
      content: string,
      skillId: string | null = null,
      onAccepted?: () => void,
      attachmentIds: string[] = [],
      knowledgeBaseIds: string[] = [],
      target?: { conversationId: string; queue: ConversationQueue; modelId: string },
      onSubmitted?: () => void,
      imageAttachmentIds: string[] = [],
      onAttachmentsPrepared?: (attachments: AttachmentSummary[]) => void,
    ) => {
      const snapshot = sessionRef.current;
      const cleanText = content.trim();
      const displayText = environmentChatDisplay(mcpChatDisplay(cleanText));
      const startingConversationId = target?.conversationId ?? snapshot.conversationId;
      const pendingQuestion = target
        ? chatCacheRef.current.get(target.conversationId)?.userQuestion
        : chatStateRef.current.userQuestion;
      const canReplaceExpiredQuestion = Boolean(
        pendingQuestion && isUserQuestionExpired(pendingQuestion.expires_at),
      );
      // 并发会话可同时跑：只看当前可见会话是否有流，不看全局 busy。
      const visibleRun = startingConversationId
        ? activeRunsRef.current.get(startingConversationId)
        : undefined;
      const visibleRunning = Boolean(
        visibleRun && !visibleRun.controller.signal.aborted,
      );
      if (
        (!cleanText && attachmentIds.length === 0) ||
        (visibleRunning && !canReplaceExpiredQuestion) ||
        (!target && snapshot.conversationCreating)
      ) return;
      if (!snapshot.contextReady || snapshot.status?.status !== "ready") {
        message.error("服务仍在准备中，请稍候再发送。");
        return;
      }
      const startUserId = snapshot.userId;
      const startProjectId = target?.queue.projectId ?? snapshot.projectId;
      const startModelId = target?.modelId ?? (snapshot.selectedModelId || "");
      const startModel = snapshot.modelOptions?.find((model) => model.available && model.id === startModelId);
      const preparationKey = JSON.stringify([startUserId, startProjectId, startingConversationId]);
      if (attachmentPreparationsRef.current.has(preparationKey)) return;
      if (imageAttachmentIds.length > 0 && startModel && !startModel.input_modalities?.includes("image")) {
        const preparations = attachmentPreparationsRef.current;
        preparations.add(preparationKey);
        refreshQueue();
        try {
          const results = await Promise.allSettled(imageAttachmentIds.map((id) => retryAttachmentParse(id, {
            userId: startUserId,
            projectId: startProjectId || null,
            conversationId: startProjectId ? null : startingConversationId,
            modelId: startModelId,
          })));
          onAttachmentsPrepared?.(results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []));
          for (const result of results) {
            if (result.status === "rejected") throw result.reason;
            if (result.value.parse_status !== "processed") {
              throw new Error(result.value.parse_error_message ?? (
                result.value.parse_status === "processing" ? "图片正在识别，请稍候再发送。" : "图片文字识别失败，请重新解析。"
              ));
            }
          }
        } catch (error) {
          const reason = error instanceof Error ? error.message : "图片文字识别失败，请重试。";
          if (target) target.queue.blocked = { reason, attachmentPreparationFailed: true };
          if (sessionRef.current.userId === startUserId) message.error(reason);
          return;
        } finally {
          preparations.delete(preparationKey);
          refreshQueue();
        }
        const current = sessionRef.current.getCurrentContext?.() ?? sessionRef.current;
        if (current.userId !== startUserId || (!target && current.epoch !== snapshot.epoch)) return;
      }
      const localSubmissionId = startingConversationId ? null : `local:${createRequestId()}`;
      if (localSubmissionId) {
        snapshot.markConversationSubmitted(localSubmissionId, startProjectId, displayText, true);
      }
      onSubmitted?.();
      let conversationId = startingConversationId;
      let creationEpoch = snapshot.epoch;
      if (!conversationId) {
        const creating = snapshot.ensureConversation();
        creationEpoch = (sessionRef.current.getCurrentContext?.() ?? sessionRef.current).epoch;
        const conversation = await creating;
        conversationId = conversation?.id ?? null;
      }
      const current = sessionRef.current.getCurrentContext?.() ?? sessionRef.current;
      if (
        !conversationId ||
        startUserId !== current.userId ||
        (target
          ? target.queue.userId !== current.userId || queuesRef.current.get(conversationId) !== target.queue
          : conversationId !== current.conversationId || startProjectId !== current.projectId)
      ) {
        if (localSubmissionId) sessionRef.current.cancelConversationSubmission(localSubmissionId);
        if (!conversationId && !current.conversationId && current.epoch === creationEpoch &&
          current.userId === startUserId && current.projectId === startProjectId) {
          dispatch({ type: "streamFailed", message: displayText });
        }
        return;
      }
      if (localSubmissionId) {
        sessionRef.current.identifyConversationSubmission(localSubmissionId, conversationId);
      }
      const context: SendContext = {
        epoch: current.epoch,
        conversationId,
        userId: current.userId,
        projectId: startProjectId,
        modelId: startModelId,
        skillId,
        draft: target ? "" : displayText,
        attachmentIds,
        knowledgeBaseIds: [...knowledgeBaseIds],
        queued: Boolean(target),
        firstMessage: !target && current.draftConversationId === conversationId,
        onAccepted,
      };
      if (!startingConversationId) {
        const reset = { type: "reset" as const, conversationId };
        chatStateRef.current = reducer(chatStateRef.current, reset);
        dispatch(reset);
      }
      const requestId = createRequestId();
      const optimisticIds = [
        `optimistic-user-${requestId}`,
        `optimistic-assistant-${requestId}`,
      ];
      // 新会话历史与首次发送可能并发；迟到的历史不能覆盖本次乐观消息。
      if (matchesContext(context)) historyControllerRef.current?.abort();
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
      if (matchesContext(context)) scroll.scrollToBottom(true);
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
              knowledgeBaseIds: context.knowledgeBaseIds,
            },
            handlers,
          ),
        context,
        optimisticIds,
      );
    },
    [dispatchFor, matchesContext, message, runStream, scroll],
  );

  const drainQueue = useCallback((conversationId: string) => {
    const queue = queuesRef.current.get(conversationId);
    const item = queue?.items[0];
    if (!queue || !item || queue.blocked || queue.sending || queue.checking ||
      activeRunsRef.current.has(conversationId) || queue.userId !== sessionRef.current.userId) return;
    queue.ready = false;
    queue.sending = item.id;
    refreshQueue();
    let accepted = false;
    void sendNow(item.content, item.skillId, () => {
      accepted = true;
      queue.items = queue.items.filter((entry) => entry.id !== item.id);
      refreshQueue();
    }, item.attachmentIds, item.knowledgeBaseIds, { conversationId, queue, modelId: item.modelId },
    undefined, item.imageAttachmentIds)
      .finally(() => {
        queue.sending = undefined;
        if (!accepted && !queue.blocked) {
          queue.blocked = { reason: "队首消息未确认发送" };
        }
        refreshQueue();
        if (queue.ready) drainQueueRef.current(conversationId);
      });
  }, [sendNow]);
  drainQueueRef.current = drainQueue;

  const sendMessage = useCallback<ChatStreamHandle["sendMessage"]>(async (
    content, skillId, onAccepted, attachmentIds = [], knowledgeBaseIds = [], onSubmitted,
    imageAttachmentIds = [], onAttachmentsPrepared,
  ) => {
    const snapshot = sessionRef.current;
    const conversationId = snapshot.conversationId;
    const run = conversationId ? activeRunsRef.current.get(conversationId) : undefined;
    let queue = conversationId ? queuesRef.current.get(conversationId) : undefined;
    if (queue?.userId !== snapshot.userId) queue = undefined;
    const chat = chatStateRef.current.conversationId === conversationId
      ? chatStateRef.current : chatCacheRef.current.get(conversationId ?? "");
    const question = chat?.userQuestion;
    const waiting = Boolean(chat?.approval || (question && !isUserQuestionExpired(question.expires_at)));
    if (!conversationId || (!queue?.items.length && !queue?.blocked && !waiting &&
      (!run || (question && isUserQuestionExpired(question.expires_at))))) {
      return sendNow(
        content, skillId, onAccepted, attachmentIds, knowledgeBaseIds,
        undefined, onSubmitted, imageAttachmentIds, onAttachmentsPrepared,
      );
    }
    if (!content.trim() && attachmentIds.length === 0) return;
    if (!queue) {
      queue = { userId: snapshot.userId, projectId: snapshot.projectId, items: [] };
      queuesRef.current.set(conversationId, queue);
    }
    queue.items.push({
      id: createRequestId(), content, preview: environmentChatDisplay(mcpChatDisplay(content)),
      modelId: snapshot.modelOptions?.find((model) => model.available && model.id === snapshot.selectedModelId)?.id
        ?? snapshot.modelOptions?.find((model) => model.available)?.id ?? "",
      skillId,
      attachmentIds: [...attachmentIds], knowledgeBaseIds: [...knowledgeBaseIds],
      imageAttachmentIds: [...imageAttachmentIds],
    });
    onSubmitted?.();
    onAccepted?.();
    refreshQueue();
  }, [sendNow]);

  const moveQueued = useCallback((id: string, targetId: string) => {
    const current = sessionRef.current;
    const queue = queuesRef.current.get(current.conversationId ?? "");
    if (!queue || queue.userId !== current.userId || id === targetId ||
      queue.sending === id || queue.sending === targetId) return;
    const from = queue.items.findIndex((item) => item.id === id);
    const to = queue.items.findIndex((item) => item.id === targetId);
    if (from < 0 || to < 0) return;
    const [item] = queue.items.splice(from, 1);
    queue.items.splice(to, 0, item);
    refreshQueue();
  }, []);

  const removeQueued = useCallback((id: string) => {
    const current = sessionRef.current;
    const queue = queuesRef.current.get(current.conversationId ?? "");
    if (!queue || queue.userId !== current.userId || queue.sending === id) return;
    queue.items = queue.items.filter((item) => item.id !== id);
    if (!queue.items.length && queue.blocked?.attachmentPreparationFailed) queue.blocked = undefined;
    refreshQueue();
  }, []);

  const sendQueued = useCallback(async () => {
    const current = sessionRef.current;
    const conversationId = current.conversationId;
    const queue = queuesRef.current.get(conversationId ?? "");
    if (!conversationId || !queue?.blocked || queue.userId !== current.userId ||
      queue.checking || queue.sending || activeRunsRef.current.has(conversationId)) return;
    const { messageId, attachmentPreparationFailed } = queue.blocked;
    if (!messageId && !(attachmentPreparationFailed && queue.items.length)) return;
    queue.checking = true;
    refreshQueue();
    try {
      const history = await getConversationHistory({ conversationId, userId: current.userId, limit: 100 });
      const latest = sessionRef.current.getCurrentContext?.() ?? sessionRef.current;
      if (latest.epoch !== current.epoch || latest.conversationId !== conversationId ||
        latest.userId !== current.userId || queuesRef.current.get(conversationId) !== queue) return;
      const previous = history.items.find((item) => item.id === messageId);
      const hasUnfinishedReply = history.items.some((item) =>
        item.role === "assistant" && !item.display_metadata?.task_run && !isTerminalStatus(item.status),
      );
      if (history.pending_approval || history.pending_interaction || hasUnfinishedReply ||
        (messageId && (!previous || !isTerminalStatus(previous.status)))) {
        message.info("尚未确认上一轮结束，待发送消息已保留。");
        return;
      }
      queue.blocked = undefined;
    } catch {
      message.error("暂时无法确认上一轮状态，请稍后再试。");
    } finally {
      queue.checking = false;
      refreshQueue();
    }
    drainQueue(conversationId);
  }, [drainQueue, message]);

  useEffect(() => {
    const queues = queuesRef.current;
    return () => queues.clear();
  }, [session.userId]);

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
        assistantMessageId,
        skillId: null,
        draft: "",
        attachmentIds: [],
        knowledgeBaseIds: [],
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
        assistantMessageId: question.assistant_message_id,
        skillId: null,
        draft: "",
        attachmentIds: [],
        knowledgeBaseIds: [],
      };
      const succeeded = await runStream(
        (handlers) =>
          sendUserInputStream(
            conversationId,
            {
              userId: context.userId,
              interactionId: question.interaction_id,
              assistantMessageId: question.assistant_message_id,
              decisionRequestId: createRequestId(),
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
            taskId: cached.conversationTaskId,
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
            taskId: cached.conversationTaskId,
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
                if (historyMessage.taskContext?.task_run) return historyMessage;
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
          taskId: data.conversation?.task_id ?? null,
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

  const refreshTaskHistory = useCallback(async (signal: AbortSignal) => {
    const snapshot = sessionRef.current;
    const conversationId = snapshot.conversationId;
    if (!conversationId) return;
    const current = chatStateRef.current;
    if (!current.conversationTaskId) return;
    const query = new URLSearchParams({ after_seq: String(Math.max(0, ...current.messages.map(item => item.seq ?? 0))) });
    for (const item of current.messages) if (item.taskContext?.task_run && item.role === "assistant" && item.status === "pending") query.append("pending_message_ids", item.id);
    const data = await apiRequest<{ conversation_id: string; items: Message[] }>(`/api/tasks/${current.conversationTaskId}/messages?${query}`, { signal });
    if (signal.aborted || sessionRef.current.epoch !== snapshot.epoch || sessionRef.current.conversationId !== conversationId) return;
    dispatchFor(conversationId, { type: "taskHistorySynced", conversationId,
      messages: data.items.map(item => historyMessage(item, Boolean(activeRunsRef.current.get(conversationId)))) });
  }, [dispatchFor]);
  useVisibleRefresh(refreshTaskHistory, 3000, active && Boolean(state.conversationTaskId));

  /**
   * 停止某会话的本地输出：abort 该会话的 SSE 订阅并乐观置 cancelled。
   * 服务端 persistent_stream 会继续执行并落库；这里不取消后端业务执行。
   * 只动该会话的本地状态，不影响其他并发会话；任务取消使用独立接口。
   */
  const stopConversation = useCallback((conversationId: string): boolean => {
    if (!conversationId) return false;
    const run = activeRunsRef.current.get(conversationId);
    if (!run || run.controller.signal.aborted) return false;
    flushTextDeltas(conversationId);
    const queue = queuesRef.current.get(conversationId) ?? {
      userId: run.context.userId, projectId: run.context.projectId, items: [],
    };
    queue.blocked = { reason: "已停止接收回复" };
    queuesRef.current.set(conversationId, queue);
    refreshQueue();
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

  const selectedQueue = session.conversationId ? queuesRef.current.get(session.conversationId) : undefined;
  const visibleQueue = selectedQueue?.userId === session.userId ? selectedQueue : undefined;
  const handle: ChatStreamHandle = {
    state,
    queue: visibleQueue,
    moveQueued,
    removeQueued,
    sendQueued,
    isRunning,
    preparingAttachments: attachmentPreparationsRef.current.has(
      JSON.stringify([session.userId, session.projectId, session.conversationId]),
    ),
    runningConversationIds,
    stopCurrent,
    sendMessage,
    submitApproval,
    submitUserInput,
    clearRestoreDraft: () => dispatch({ type: "draftRestored" }),
    reloadHistory,
    loadMessage,
  };

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
