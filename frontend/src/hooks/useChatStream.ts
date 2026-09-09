import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type Dispatch,
} from "react";
import { App as AntdApp } from "antd";

import { getConversationHistory } from "../api/client";
import { sendApprovalStream, sendMessageStream } from "../api/stream";
import type {
  ApprovalDecision,
  DisplayEvent,
  MessageStatus,
  PendingApproval,
  StreamEvent,
} from "../types/api";
import { classifyToolSelectorText } from "../lib/toolSelection";
import { useSession } from "../state/session";

/** 聊天视图内的消息模型（乐观消息与历史消息统一表示）。 */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** 流式期间为纯文本累加；completed 后整段替换。 */
  content: string;
  /** null = 未知/乐观；"streaming" = 流式进行中。 */
  status: MessageStatus | "streaming" | null;
  timestamp?: string | null;
  /** completed 的助手消息按 Markdown 渲染。 */
  markdown: boolean;
  /** 工具/子代理轨迹事件，与最终回复分层展示。 */
  events: DisplayEvent[];
  /** 乐观渲染的临时消息（未收到 message_started 前）。 */
  optimistic?: boolean;
}

interface ChatState {
  conversationId: string | null;
  conversationTitle: string | null;
  messages: ChatMessage[];
  approval: PendingApproval | null;
  historyLoading: boolean;
  /** 失败时需要回填的草稿（仅当输入框为空时生效，对齐旧 restoreDraft）。 */
  restoreDraft: string | null;
  error: string | null;
}

type ChatAction =
  | { type: "reset"; conversationId: string | null }
  | { type: "historyLoading"; conversationId: string | null }
  | {
      type: "historyLoaded";
      conversationId: string;
      title: string | null;
      messages: ChatMessage[];
      approval: PendingApproval | null;
    }
  | { type: "optimistic"; conversationId: string; user: ChatMessage; assistant: ChatMessage }
  | { type: "removeOptimistic"; ids: string[] }
  | {
      type: "messageStarted";
      assistantMessageId: string;
      userMessageId: string | null;
      resuming?: boolean;
    }
  | { type: "text"; text: string }
  | { type: "displayEvent"; event: DisplayEvent }
  | { type: "completed"; messageId: string; content: string }
  | { type: "messageStatus"; messageId: string; status: MessageStatus }
  | { type: "streamFailed"; messageId?: string; message?: string; error?: string }
  | { type: "historyFailed"; error: string }
  | { type: "approvalRequired"; request: PendingApproval }
  | { type: "approvalCleared" }
  | { type: "draftRestored" };

export const INITIAL_CHAT_STATE: ChatState = {
  conversationId: null,
  conversationTitle: null,
  messages: [],
  approval: null,
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

export function reducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "reset":
      return { ...INITIAL_CHAT_STATE, conversationId: action.conversationId };
    case "historyLoading":
      return {
        ...INITIAL_CHAT_STATE,
        conversationId: action.conversationId,
        historyLoading: true,
      };
    case "historyLoaded":
      return {
        ...state,
        conversationId: action.conversationId,
        conversationTitle: action.title,
        messages: action.messages,
        approval: action.approval,
        historyLoading: false,
        restoreDraft: null,
        error: null,
      };
    case "optimistic":
      return {
        ...state,
        conversationId: action.conversationId,
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
        error: null,
        messages: state.messages.map((message) => {
          if (message.role === "assistant" && (message.optimistic || message.id === action.assistantMessageId)) {
            return { ...message, id: action.assistantMessageId, status: "streaming", optimistic: false };
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
        messages: upsertLastAssistant(state, (message) => ({
          ...message,
          content: message.content + action.text,
          status: message.status === "streaming" ? message.status : "streaming",
        })),
      };
    case "displayEvent":
      return {
        ...state,
        messages: upsertLastAssistant(state, (message) => ({
          ...message,
          events: [...message.events, action.event],
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
                status: "completed" as const,
                markdown: true,
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
            ? { ...message, id: action.messageId, status: action.status, optimistic: false }
            : message,
        ),
      };
    case "streamFailed":
      return {
        ...state,
        messages: state.messages.map((message) => message.role === "assistant" &&
          (action.messageId ? message.id === action.messageId : message.status === "streaming") ? ({
          ...message,
          status: "failed",
          markdown: true,
        }) : message),
        restoreDraft: state.restoreDraft ?? action.message ?? null,
        error: action.error ?? state.error,
      };
    case "historyFailed":
      return { ...state, historyLoading: false, error: action.error };
    case "approvalRequired":
      return { ...state, approval: action.request, messages: upsertLastAssistant(state, (message) => ({ ...message, status: "interrupted", markdown: true })) };
    case "approvalCleared":
      return { ...state, approval: null };
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
  tenantId: string;
  projectId: string;
  draft: string;
}

export interface ChatStreamHandle {
  state: ChatState;
  sendMessage: (content: string, onAccepted?: () => void) => Promise<void>;
  submitApproval: (
    decisions:
      | ApprovalDecision[]
      | { interrupt_id: string; decisions: ApprovalDecision[] }[],
  ) => Promise<void>;
  clearRestoreDraft: () => void;
  reloadHistory: () => void;
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
  const activeRunRef = useRef<{ controller: AbortController; context: SendContext } | null>(null);
  const historyControllerRef = useRef<AbortController | null>(null);
  const forceHistoryReloadRef = useRef(false);
  const selectorTextBufferRef = useRef("");

  const matchesContext = useCallback((context: SendContext): boolean => {
    const current = sessionRef.current;
    return (
      context.conversationId === current.conversationId &&
      context.userId === current.userId &&
      context.tenantId === current.tenantId &&
      context.projectId === current.projectId
    );
  }, []);

  const appendStreamText = useCallback((text: string): boolean => {
    if (!text) return false;
    const candidate = selectorTextBufferRef.current + text;
    const kind = classifyToolSelectorText(candidate);
    if (kind === "selector") {
      selectorTextBufferRef.current = "";
      return false;
    }
    if (kind === "pending") {
      selectorTextBufferRef.current = candidate;
      return false;
    }
    if (selectorTextBufferRef.current) {
      dispatch({ type: "text", text: selectorTextBufferRef.current });
      selectorTextBufferRef.current = "";
    }
    dispatch({ type: "text", text });
    return true;
  }, []);

  const reloadHistory = useCallback(() => {
    forceHistoryReloadRef.current = true;
    bumpHistoryRevision();
  }, [bumpHistoryRevision]);

  const handleEvent = useCallback(
    (event: StreamEvent, context: SendContext): void => {
      if (!matchesContext(context)) return;
      const follow = scroll.isNearBottom();
      switch (event.type) {
        case "message_started":
          dispatch({
            type: "messageStarted",
            assistantMessageId: event.message_id,
            userMessageId: event.user_message_id,
            resuming: event.resuming,
          });
          break;
        case "text":
          if (appendStreamText(event.text)) {
            sessionRef.current.setRunStatus("processing");
          }
          break;
        case "completed":
          selectorTextBufferRef.current = "";
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus(null);
          dispatch({ type: "approvalCleared" });
          dispatch({
            type: "completed",
            messageId: event.message_id,
            content: classifyToolSelectorText(event.content) === "selector" ? "" : event.content,
          });
          break;
        case "message_status":
          dispatch({
            type: "messageStatus",
            messageId: event.message_id,
            status: event.status,
          });
          if (event.status === "pending") {
            message.error("该请求正在进行中，请稍候刷新会话。");
            dispatch({ type: "historyFailed", error: "该请求仍在进行中。请稍后重新同步会话，确认结果后再发送新消息。" });
          }
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus(
            event.status === "interrupted"
              ? "waiting"
              : event.status === "failed"
                ? "failed"
                : null,
          );
          break;
        case "approval_required":
          sessionRef.current.setBusy(true);
          sessionRef.current.setRunStatus("waiting");
          dispatch({ type: "approvalRequired", request: event.request });
          break;
        case "done":
          sessionRef.current.setBusy(false);
          void sessionRef.current.refreshConversations();
          break;
        case "error":
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus("failed");
          dispatch({
            type: "streamFailed",
            messageId: event.message_id,
            message: context.draft,
            error: event.message || "助手运行失败。请检查会话状态后重试。",
          });
          message.error(event.message || "助手运行失败。请重试。");
          break;
        case "tool_call":
        case "tool_result":
        case "subagent_started":
        case "subagent_text":
        case "subagent_tool_call":
        case "subagent_tool_result":
        case "subagent_completed":
        case "subagent_failed":
          sessionRef.current.setRunStatus("processing");
          dispatch({ type: "displayEvent", event: event as DisplayEvent });
          break;
        default:
          break;
      }
      if (follow) {
        scroll.scrollToBottom();
      }
    },
    [appendStreamText, matchesContext, message, scroll],
  );

  const runStream = useCallback(
    async (
      stream: (handlers: {
        onEvent: (event: StreamEvent) => void;
        signal: AbortSignal;
      }) => Promise<void>,
      context: SendContext,
      optimisticIds: string[],
    ): Promise<boolean> => {
      if (activeRunRef.current && matchesContext(activeRunRef.current.context)) return false;
      sessionRef.current.setBusy(true);
      sessionRef.current.setRunStatus("selecting_tools");
      selectorTextBufferRef.current = "";
      let started = false;
      let failed = false;
      let terminal = false;
      const controller = new AbortController();
      activeRunRef.current = { controller, context };
      sessionRef.current.attachStream(controller);
      try {
        await stream({
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "message_started") started = true;
            if (event.type === "error") failed = true;
            if (TERMINAL_HINT.has(event.type)) terminal = true;
            if (
              !matchesContext(context) &&
              activeRunRef.current?.controller === controller
            ) {
              switch (event.type) {
                case "text":
                case "tool_call":
                case "tool_result":
                case "subagent_started":
                case "subagent_text":
                case "subagent_tool_call":
                case "subagent_tool_result":
                case "subagent_completed":
                case "subagent_failed":
                  sessionRef.current.setRunStatus("processing");
                  break;
                case "completed":
                case "done":
                  sessionRef.current.setBusy(false);
                  sessionRef.current.setRunStatus(null);
                  if (event.type === "done") {
                    void sessionRef.current.refreshConversations();
                  }
                  break;
                case "approval_required":
                  sessionRef.current.setBusy(true);
                  sessionRef.current.setRunStatus("waiting");
                  break;
                case "error":
                  sessionRef.current.setBusy(false);
                  sessionRef.current.setRunStatus("failed");
                  break;
                case "message_status":
                  sessionRef.current.setBusy(false);
                  sessionRef.current.setRunStatus(
                    event.status === "interrupted"
                      ? "waiting"
                      : event.status === "failed"
                        ? "failed"
                        : null,
                  );
                  break;
                default:
                  break;
              }
            }
            handleEvent(event, context);
          },
        });
        if (!terminal) {
          if (matchesContext(context)) {
            throw new Error("连接意外结束，回复可能不完整。请重新同步会话以确认执行结果。");
          }
          if (activeRunRef.current?.controller === controller) {
            sessionRef.current.setBusy(false);
            sessionRef.current.setRunStatus("failed");
          }
          return false;
        }
        return !failed;
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return false;
        }
        if (!started && matchesContext(context)) {
          dispatch({ type: "removeOptimistic", ids: optimisticIds });
        }
        if (matchesContext(context)) {
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus("failed");
          dispatch({ type: "streamFailed", message: context.draft, error: error instanceof Error ? error.message : String(error) });
          message.error(error instanceof Error ? error.message : String(error));
        }
        return false;
      } finally {
        selectorTextBufferRef.current = "";
        if (activeRunRef.current?.controller === controller) {
          activeRunRef.current = null;
          if (matchesContext(context)) sessionRef.current.attachStream(null);
        }
      }
    },
    [handleEvent, matchesContext, message],
  );

  const sendMessage = useCallback(
    async (content: string, onAccepted?: () => void) => {
      const snapshot = sessionRef.current;
      const cleanText = content.trim();
      if (!cleanText || snapshot.busy || snapshot.conversationCreating) return;
      if (activeRunRef.current && matchesContext(activeRunRef.current.context)) return;
      if (!snapshot.contextReady || snapshot.status?.status !== "ready") {
        message.error("服务仍在准备中，请稍候再发送。");
        return;
      }
      if (snapshot.projects.length === 0) {
        message.error("还没有可用项目，请先创建一个项目。");
        return;
      }
      const startUserId = snapshot.userId;
      const startTenantId = snapshot.tenantId;
      const startProjectId = snapshot.projectId;
      let conversationId = snapshot.conversationId;
      if (!conversationId) {
        const conversation = await snapshot.newConversation();
        conversationId = conversation?.id ?? null;
      }
      if (
        !conversationId ||
        conversationId !== sessionRef.current.conversationId ||
        startUserId !== sessionRef.current.userId ||
        startTenantId !== sessionRef.current.tenantId ||
        (startProjectId && startProjectId !== sessionRef.current.projectId)
      ) {
        return;
      }
      const context: SendContext = {
        epoch: sessionRef.current.epoch,
        conversationId,
        userId: sessionRef.current.userId,
        tenantId: sessionRef.current.tenantId,
        projectId: sessionRef.current.projectId,
        draft: cleanText,
      };
      const requestId = crypto.randomUUID();
      const optimisticIds = [
        `optimistic-user-${requestId}`,
        `optimistic-assistant-${requestId}`,
      ];
      // 新会话历史与首次发送可能并发；迟到的历史不能覆盖本次乐观消息。
      historyControllerRef.current?.abort();
      onAccepted?.();
      dispatch({
        type: "optimistic",
        conversationId,
        user: {
          id: optimisticIds[0],
          role: "user",
          content: cleanText,
          status: null,
          timestamp: new Date().toISOString(),
          markdown: false,
          events: [],
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
          optimistic: true,
        },
      });
      scroll.scrollToBottom(true);
      await runStream(
        (handlers) =>
          sendMessageStream(
            conversationId,
            {
              userId: context.userId,
              tenantId: context.tenantId,
              requestId,
              content: cleanText,
            },
            handlers,
          ),
        context,
        optimisticIds,
      );
    },
    [message, runStream, scroll, matchesContext],
  );

  const submitApproval = useCallback(
    async (
      decisions:
        | ApprovalDecision[]
        | { interrupt_id: string; decisions: ApprovalDecision[] }[],
    ) => {
      const snapshot = sessionRef.current;
      const conversationId = snapshot.conversationId;
      if (!conversationId) return;
      historyControllerRef.current?.abort();
      const context: SendContext = {
        epoch: snapshot.epoch,
        conversationId,
        userId: snapshot.userId,
        tenantId: snapshot.tenantId,
        projectId: snapshot.projectId,
        draft: "",
      };
      const succeeded = await runStream(
        (handlers) =>
          sendApprovalStream(conversationId, { userId: context.userId, tenantId: context.tenantId, decisions }, handlers),
        context,
        [],
      );
      if (!succeeded && matchesContext(context)) {
        throw new Error(
          "决定未能完成提交或运行已中断。请重新同步会话，确认当前审批状态后再试。",
        );
      }
    },
    [runStream, matchesContext],
  );

  // 切换会话/用户/项目时加载历史消息（含 pending_approval 恢复）。
  useEffect(() => {
    const snapshot = session;
    const controller = new AbortController();
    historyControllerRef.current = controller;
    const load = async () => {
      const forceReload = forceHistoryReloadRef.current;
      forceHistoryReloadRef.current = false;
      const activeRun = activeRunRef.current;
      if (
        !forceReload &&
        activeRun &&
        !activeRun.controller.signal.aborted &&
        matchesContext(activeRun.context) &&
        chatStateRef.current.conversationId === snapshot.conversationId
      ) return;
      if (!snapshot.conversationId) {
        dispatch({ type: "reset", conversationId: null });
        return;
      }
      dispatch({ type: "historyLoading", conversationId: snapshot.conversationId });
      try {
        const data = await getConversationHistory(
          {
            conversationId: snapshot.conversationId,
            userId: snapshot.userId,
            tenantId: snapshot.tenantId,
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
        const messages: ChatMessage[] = data.items.flatMap((item) => {
          const content = item.content || "";
          if (item.role !== "user" && classifyToolSelectorText(content) === "selector") {
            return [];
          }
          return {
            id: item.id,
            role: item.role === "user" ? "user" : "assistant",
            content,
            status: item.status,
            timestamp: item.created_at ?? null,
            markdown: item.role !== "user",
            events: item.display_metadata?.events ?? [],
          };
        });
        dispatch({
          type: "historyLoaded",
          conversationId: snapshot.conversationId,
          title: data.conversation?.title ?? null,
          messages,
          approval: data.pending_approval,
        });
        const currentActiveRun = activeRunRef.current;
        const activeStream =
          currentActiveRun && !currentActiveRun.controller.signal.aborted;
        const activeForSnapshot =
          activeStream && matchesContext(currentActiveRun.context);
        if (data.pending_approval) {
          sessionRef.current.setBusy(true);
          sessionRef.current.setRunStatus("waiting");
        } else if (!activeStream) {
          sessionRef.current.setBusy(false);
          const pending = messages.some((item) => item.status === "pending");
          sessionRef.current.setRunStatus(pending ? "processing" : null);
          if (pending) {
            dispatch({ type: "historyFailed", error: "上次请求尚未结束，当前未连接其输出。请稍后重新同步会话。" });
          }
        } else if (activeForSnapshot) {
          // 回到仍在运行的会话：历史只提供已落库内容，后续事件继续由原 SSE 补上。
          sessionRef.current.setBusy(true);
          sessionRef.current.setRunStatus("processing");
        }
      } catch (error) {
        if (controller.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
          return;
        }
        if (snapshot.epoch === sessionRef.current.epoch) {
          message.error(error instanceof Error ? error.message : String(error));
          dispatch({ type: "historyFailed", error: error instanceof Error ? error.message : String(error) });
        }
      }
    };
    void load();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.conversationId, session.userId, session.tenantId, session.projectId, session.epoch, historyRevision]);

  const handle = useMemo<ChatStreamHandle>(
    () => ({
      state,
      sendMessage,
      submitApproval,
      clearRestoreDraft: () => dispatch({ type: "draftRestored" }),
      reloadHistory,
    }),
    [state, reloadHistory, sendMessage, submitApproval],
  );

  return handle;
}

const TERMINAL_HINT = new Set([
  "completed",
  "error",
  "approval_required",
  "message_status",
]);

export type ChatDispatch = Dispatch<ChatAction>;
