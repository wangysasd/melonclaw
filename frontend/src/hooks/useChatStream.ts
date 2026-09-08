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
  /** 工具/子代理轨迹事件（下一迁移步骤接入 ThoughtChain 渲染）。 */
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
  | { type: "optimistic"; user: ChatMessage; assistant: ChatMessage }
  | { type: "removeOptimistic"; ids: string[] }
  | {
      type: "messageStarted";
      assistantMessageId: string;
      userMessageId: string | null;
    }
  | { type: "text"; text: string }
  | { type: "displayEvent"; event: DisplayEvent }
  | { type: "completed"; messageId: string; content: string }
  | { type: "messageStatus"; messageId: string; status: MessageStatus }
  | { type: "streamFailed"; messageId?: string; message?: string }
  | { type: "approvalRequired"; request: PendingApproval }
  | { type: "approvalCleared" }
  | { type: "draftRestored" };

const INITIAL_CHAT_STATE: ChatState = {
  conversationId: null,
  conversationTitle: null,
  messages: [],
  approval: null,
  historyLoading: false,
  restoreDraft: null,
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

function reducer(state: ChatState, action: ChatAction): ChatState {
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
      };
    case "optimistic":
      return {
        ...state,
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
        messages: state.messages.map((message) => {
          if (message.role === "assistant" && message.optimistic) {
            return { ...message, id: action.assistantMessageId, optimistic: false };
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
        messages: upsertLastAssistant(state, (message) => ({
          ...message,
          status: "failed",
        })),
        restoreDraft: state.restoreDraft ?? action.message ?? null,
      };
    case "approvalRequired":
      return { ...state, approval: action.request };
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
  sendMessage: (content: string) => Promise<void>;
  submitApproval: (
    decisions:
      | ApprovalDecision[]
      | { interrupt_id: string; decisions: ApprovalDecision[] }[],
  ) => Promise<void>;
  clearRestoreDraft: () => void;
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

  const sessionRef = useRef(session);
  sessionRef.current = session;
  const startedRef = useRef(false);

  const matchesContext = useCallback((context: SendContext): boolean => {
    const current = sessionRef.current;
    return (
      context.epoch === current.epoch &&
      context.conversationId === current.conversationId &&
      context.userId === current.userId &&
      context.tenantId === current.tenantId &&
      context.projectId === current.projectId
    );
  }, []);

  const handleEvent = useCallback(
    (event: StreamEvent, context: SendContext): void => {
      if (!matchesContext(context)) return;
      const follow = scroll.isNearBottom();
      switch (event.type) {
        case "message_started":
          startedRef.current = true;
          dispatch({
            type: "messageStarted",
            assistantMessageId: event.message_id,
            userMessageId: event.user_message_id,
          });
          break;
        case "text":
          dispatch({ type: "text", text: event.text });
          break;
        case "completed":
          dispatch({
            type: "completed",
            messageId: event.message_id,
            content: event.content,
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
          sessionRef.current.setRunStatus(null);
          dispatch({ type: "approvalCleared" });
          void sessionRef.current.refreshConversations();
          break;
        case "error":
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus("failed");
          dispatch({
            type: "streamFailed",
            messageId: event.message_id,
            message: context.draft,
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
          dispatch({ type: "displayEvent", event: event as DisplayEvent });
          break;
        default:
          break;
      }
      if (follow) {
        scroll.scrollToBottom();
      }
    },
    [matchesContext, message, scroll],
  );

  const runStream = useCallback(
    async (
      stream: (handlers: {
        onEvent: (event: StreamEvent) => void;
        signal: AbortSignal;
      }) => Promise<void>,
      context: SendContext,
      optimisticIds: string[],
    ): Promise<void> => {
      sessionRef.current.setBusy(true);
      sessionRef.current.setRunStatus("processing");
      startedRef.current = false;
      let terminal = false;
      const controller = new AbortController();
      sessionRef.current.attachStream(controller);
      try {
        await stream({
          signal: controller.signal,
          onEvent: (event) => {
            if (TERMINAL_HINT.has(event.type)) terminal = true;
            handleEvent(event, context);
          },
        });
        if (!terminal && matchesContext(context)) {
          // 流结束但没有任何终止事件：收敛 busy 并提示。
          sessionRef.current.setBusy(false);
          message.error("流连接已结束，但服务器没有返回完成状态。请刷新会话后重试。");
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (!startedRef.current && matchesContext(context)) {
          dispatch({ type: "removeOptimistic", ids: optimisticIds });
        }
        if (matchesContext(context)) {
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus("failed");
          dispatch({ type: "streamFailed", message: context.draft });
          message.error(error instanceof Error ? error.message : String(error));
        }
      } finally {
        sessionRef.current.attachStream(null);
      }
    },
    [handleEvent, matchesContext, message],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      const snapshot = sessionRef.current;
      const cleanText = content.trim();
      if (!cleanText || snapshot.busy || snapshot.conversationCreating) return;
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
        startUserId !== sessionRef.current.userId ||
        startTenantId !== sessionRef.current.tenantId ||
        (startProjectId && startProjectId !== sessionRef.current.projectId)
      ) {
        dispatch({ type: "streamFailed", message: cleanText });
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
      dispatch({
        type: "optimistic",
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
    [message, runStream, scroll],
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
      const context: SendContext = {
        epoch: snapshot.epoch,
        conversationId,
        userId: snapshot.userId,
        tenantId: snapshot.tenantId,
        projectId: snapshot.projectId,
        draft: "",
      };
      await runStream(
        (handlers) =>
          sendApprovalStream(conversationId, { userId: context.userId, tenantId: context.tenantId, decisions }, handlers),
        context,
        [],
      );
    },
    [runStream],
  );

  // 切换会话/用户/项目时加载历史消息（含 pending_approval 恢复）。
  useEffect(() => {
    const snapshot = session;
    const controller = new AbortController();
    const load = async () => {
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
        const messages: ChatMessage[] = data.items.map((item) => ({
          id: item.id,
          role: item.role === "user" ? "user" : "assistant",
          content: item.content || "",
          status: item.status,
          timestamp: item.created_at ?? null,
          markdown: item.role !== "user",
          events: item.display_metadata?.events ?? [],
        }));
        dispatch({
          type: "historyLoaded",
          conversationId: snapshot.conversationId,
          title: data.conversation?.title ?? null,
          messages,
          approval: data.pending_approval,
        });
        if (data.pending_approval) {
          sessionRef.current.setBusy(true);
          sessionRef.current.setRunStatus("waiting");
        } else {
          sessionRef.current.setBusy(false);
          sessionRef.current.setRunStatus(null);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (snapshot.epoch === sessionRef.current.epoch) {
          message.error(error instanceof Error ? error.message : String(error));
          dispatch({ type: "reset", conversationId: snapshot.conversationId });
        }
      }
    };
    void load();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.conversationId, session.userId, session.tenantId, session.projectId]);

  const handle = useMemo<ChatStreamHandle>(
    () => ({
      state,
      sendMessage,
      submitApproval,
      clearRestoreDraft: () => dispatch({ type: "draftRestored" }),
    }),
    [state, sendMessage, submitApproval],
  );

  return handle;
}

const TERMINAL_HINT = new Set([
  "done",
  "error",
  "approval_required",
  "message_status",
]);

export type ChatDispatch = Dispatch<ChatAction>;
