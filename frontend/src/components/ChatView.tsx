import { Spin } from "antd";
import Bubble from "@ant-design/x/es/bubble";
import Prompts from "@ant-design/x/es/prompts";
import { memo, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "./Icon";
import { Composer } from "./Composer";
import { ApprovalPanel } from "./ApprovalPanel";
import { UserQuestionPanel } from "./UserQuestionPanel";
import { ImageLightbox } from "./ImageLightbox";
import { Markdown } from "./Markdown";
import { AgentExecution } from "./AgentExecution";
import { ToolCatalogDialog } from "./ToolCatalogDialog";
import { useChatStream, type ChatMessage } from "../hooks/useChatStream";
import { useServiceStatus } from "../hooks/useServiceStatus";
import { buildAgentRun } from "../lib/agentRun";
import { attachmentBadge } from "../lib/attachmentFiles";
import { copyText } from "../lib/clipboard";
import { formatMessageTime } from "../lib/format";
import { useUserQuestionExpired } from "../lib/userQuestionExpiry";
import { useSession, type RunStatus } from "../state/session";
import { attachmentContentUrl } from "../api/client";
import type { PendingApproval, UserQuestionRequest } from "../types/api";

const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  starting: "正在准备",
  ready: "已就绪",
  selecting_tools: "正在工具筛选",
  thinking: "思考中",
  responding: "正在生成回复",
  processing: "处理中",
  waiting: "等待确认",
  failed: "失败",
};

/** 审批面板重挂载 key：interrupt ID 组合变化时重置面板内部表单状态。 */
function approvalKey(approval: PendingApproval): string {
  const binding = `${approval.approval_batch_id ?? ""}:${approval.assistant_message_id ?? ""}`;
  if (Array.isArray(approval.interrupts) && approval.interrupts.length > 0) {
    return `${binding}:${approval.interrupts.map((item) => item.id).join(",")}`;
  }
  return `${binding}:${approval.id ?? "single"}`;
}

/**
 * 问题卡片重挂载 key：interaction ID 或题目变化时重置卡片内部表单状态。
 *
 * 只认 interaction_id 不够：助手连续提问时账本可能被复用，题目换了但 ID 不变，
 * 卡片不重建就会把上一道题的选择留给下一道题。
 */
function questionKey(question: UserQuestionRequest): string {
  const digest = question.questions
    .map((item) => `${item.id}:${item.question}`)
    .join(",");
  return `${question.interaction_id}:${digest}`;
}

const WELCOME_PROMPTS = [
  {
    key: "search",
    icon: <Icon name="search" size={18} className="prompt-icon" />,
    label: "查找资料",
    description: "比较方案，梳理可靠信息",
    prompt: "请比较两个技术方案的优缺点，并给出适用场景和推荐结论。",
  },
  {
    key: "organize",
    icon: <Icon name="list-checks" size={18} className="prompt-icon" />,
    label: "整理思路",
    description: "把杂乱内容变成行动清单",
    prompt: "请把下面这段内容整理成清晰的要点清单，并标出待确认的问题。",
  },
  {
    key: "plan",
    icon: <Icon name="calendar-days" size={18} className="prompt-icon" />,
    label: "制定计划",
    description: "拆解目标，安排节奏与下一步",
    prompt: "请根据我的目标制定一周学习计划，安排每天的重点、时间和复盘方式。",
  },
];

function MessageAttachments({
  attachments,
  userId,
  tenantId,
  projectId,
  conversationId,
}: {
  attachments: NonNullable<ChatMessage["attachments"]>;
  userId: string;
  tenantId: string;
  projectId: string;
  conversationId: string | null;
}) {
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);
  if (attachments.length === 0) return null;
  return (
    <>
      <div className="message-attachments" aria-label="消息附件">
        {attachments.map((attachment) => {
          const url = attachmentContentUrl(attachment.attachment_id, {
            userId,
            tenantId,
            projectId: projectId || null,
            conversationId: projectId ? null : conversationId,
          });
          const isImage = attachment.kind === "image";
          return (
            <div className="message-attachment" key={attachment.attachment_id}>
              {isImage ? (
                <button
                  type="button"
                  className="message-attachment-thumb"
                  aria-label={`预览 ${attachment.file_name}`}
                  onClick={() => setPreview({ url, name: attachment.file_name })}
                >
                  <img src={url} alt={attachment.file_name} />
                </button>
              ) : (
                <span className="attachment-file-icon" aria-hidden="true">
                  <Icon name="file-text" size={18} />
                  <span className="attachment-badge">{attachmentBadge(attachment.file_name)}</span>
                </span>
              )}
              <div className="message-attachment-info">
                <a href={url} target="_blank" rel="noreferrer" download={attachment.file_name}>{attachment.file_name}</a>
                <span>{attachment.parse_status === "failed" ? "解析失败" : isImage ? "图片" : "文档"}</span>
              </div>
            </div>
          );
        })}
      </div>
      {preview ? (
        <ImageLightbox src={preview.url} alt={preview.name} onClose={() => setPreview(null)} />
      ) : null}
    </>
  );
}

/** 助手消息的复制按钮：复制原始 Markdown 文本，带成功/失败反馈（对齐旧 handleCopyClick）。 */
function CopyButton({ message }: { message: ChatMessage }) {
  const [feedback, setFeedback] = useState<"idle" | "ok" | "fail">("idle");
  const disabled = message.status === "streaming" || !message.content;

  const handleCopy = async () => {
    try {
      await copyText(message.content);
      setFeedback("ok");
    } catch {
      setFeedback("fail");
    }
    window.setTimeout(() => setFeedback("idle"), 1800);
  };

  return (
    <button
      type="button"
      className="copy-button"
      disabled={disabled}
      onClick={() => void handleCopy()}
    >
      <Icon name="copy" size={14} />
      <span>{feedback === "ok" ? "已复制" : feedback === "fail" ? "复制失败" : "复制"}</span>
    </button>
  );
}

function MessageFooter({ message }: { message: ChatMessage }) {
  if (message.role === "user" || !message.content || message.status === "streaming") return null;
  return (
    <div className="message-actions">
      <CopyButton message={message} />
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({
  message,
  userName,
  runStatus,
  conversationId,
  userId,
  tenantId,
  projectId,
}: {
  message: ChatMessage;
  userName: string;
  runStatus: RunStatus;
  conversationId: string | null;
  userId: string;
  tenantId: string;
  projectId: string;
}) {
  const metaLabel = message.role === "user" ? userName : "MelonClaw";
  const metaDetail =
    message.role === "user"
      ? formatMessageTime(message.timestamp ?? null)
      : "";
  const run = useMemo(
    () =>
      message.role === "assistant" ? buildAgentRun(message, conversationId) : null,
    [message, conversationId],
  );
  // 执行过程与最终回答彻底分离：折叠只作用于执行过程，回答正文始终独立渲染。
  const displayContent = run ? run.finalAnswer ?? "" : message.content;
  const renderedContent = useDeferredValue(displayContent);

  return (
    <article className={`message ${message.role}`}>
      <div className={`avatar ${message.role === "user" ? "user-avatar" : "assistant-avatar"}`}>
        <img
          src={
            message.role === "user"
              ? "/assets/brand/melon.png"
              : "/assets/brand/melonclaw-mark.png"
          }
          alt={message.role === "user" ? userName : "MelonClaw"}
        />
      </div>
      <div className="message-content">
        <div className="message-meta">
          <span className="message-author">{metaLabel}</span>
          {metaDetail ? <span className="message-time">{metaDetail}</span> : null}
        </div>
        {run ? (
          <AgentExecution
            run={run}
            events={message.events}
            messageStatus={message.status}
            phaseLabel={RUN_STATUS_LABELS[runStatus]}
          />
        ) : null}
        <MessageAttachments
          attachments={message.attachments ?? []}
          userId={userId}
          tenantId={tenantId}
          projectId={projectId}
          conversationId={conversationId}
        />
        <div className="message-body">
          {message.role === "user" || displayContent ? (
            <Bubble
              placement={message.role === "user" ? "end" : "start"}
              variant={message.role === "assistant" ? "borderless" : "filled"}
              content={message.role === "assistant"
                ? <Markdown source={renderedContent} streaming={message.status === "streaming"} />
                : message.content}
            />
          ) : null}
        </div>
        {message.status === "failed" || message.status === "cancelled" ? (
          <p className="message-notice">{message.status === "failed" ? "本次回复未完成，当前显示已接收的内容。" : "本次回复已中止。"}</p>
        ) : null}
        <MessageFooter message={message} />
      </div>
    </article>
  );
});

/** 聊天主视图：消息与审批共用阅读流，底部保留输入区与状态提醒。 */
export function ChatView({ onOpenProjectDialog }: { onOpenProjectDialog?: () => void } = {}) {
  const session = useSession();
  const { runStatus, status } = useServiceStatus();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [toolCatalogOpen, setToolCatalogOpen] = useState(false);
  const scrollFrame = useRef<number | null>(null);
  const approvalRef = useRef<HTMLDivElement>(null);

  const scroll = useMemo(
    () => ({
      isNearBottom: () => {
        const element = scrollRef.current;
        if (!element) return true;
        return (
          element.scrollHeight - element.scrollTop - element.clientHeight < 96
        );
      },
      scrollToBottom: (smooth = false) => {
        if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
        scrollFrame.current = requestAnimationFrame(() => {
          scrollFrame.current = null;
          const element = scrollRef.current;
          if (!element) return;
          element.scrollTo({
            top: element.scrollHeight,
            behavior: smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "auto",
          });
        });
      },
    }),
    [],
  );

  const chat = useChatStream({ scroll });
  const userQuestionExpired = useUserQuestionExpired(
    chat.state.userQuestion?.expires_at,
  );
  const expiredQuestionCanStartNewMessage = Boolean(
    chat.state.userQuestion && userQuestionExpired,
  );
  useEffect(() => () => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
  }, []);
  // 切换会话即换一张“白纸”：旧会话没发出去的草稿不能带到新会话，否则新建
  // 会话看起来和没点一样（同样的欢迎页 + 同样的输入框文字）。失败回填走
  // restoreDraft（只在输入框为空时生效），切会话时旧会话的回填已无意义，一并丢弃。
  useEffect(() => {
    setDraft("");
    chat.clearRestoreDraft();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.state.conversationId]);
  useEffect(() => {
    if (!chat.state.historyLoading) scroll.scrollToBottom();
  }, [chat.state.conversationId, chat.state.historyLoading, scroll]);

  // 失败回滚：仅当输入框为空时回填草稿（对齐旧 restoreDraft）。
  useEffect(() => {
    if (!chat.state.restoreDraft) return;
    if (draft === "") {
      setDraft(chat.state.restoreDraft);
    }
    chat.clearRestoreDraft();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.state.restoreDraft]);

  const handleSend = (
    value: string,
    skillId?: string | null,
    attachmentIds?: string[],
    onAccepted?: () => void,
  ) => {
    void chat.sendMessage(
      value,
      skillId,
      () => {
        setDraft((current) => current === value ? "" : current);
        onAccepted?.();
      },
      attachmentIds,
    );
  };

  const hasMessages = chat.state.messages.length > 0;
  const userName =
    session.users.find((user) => user.user_id === session.userId)?.display_name ??
    (session.userId || "用户");
  const currentConversation = session.conversationId
    ? session.conversations.find((item) => item.id === session.conversationId)
      ?? session.recents.find((item) => item.id === session.conversationId)
    : null;
  const chatMatchesSelection = Boolean(session.conversationId && chat.state.conversationId === session.conversationId);
  const conversationTitle = currentConversation?.title
    || (chatMatchesSelection ? chat.state.conversationTitle : null)
    || "准备开始";
  const conversationProjectId = currentConversation
    ? currentConversation.project_id
    : chatMatchesSelection && chat.state.conversationProjectId
      ? chat.state.conversationProjectId
      : session.conversationId ? session.projectId : null;
  const projectName = session.projects.find((project) => project.id === conversationProjectId)?.name;
  const topbarTitle = projectName ? `${projectName}/${conversationTitle}` : conversationTitle;

  return (
    <div className="chat-view">
      <header className="topbar">
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button mobile-menu"
            onClick={() =>
              window.dispatchEvent(new Event("melonclaw:open-sidebar"))
            }
            aria-label="打开项目与会话导航"
            title="打开导航"
          >
            <Icon name="menu" size={18} />
          </button>
          <div className="topbar-session-name">
            {topbarTitle}
          </div>
        </div>
        <button
          type="button"
          className="tool-catalog-trigger"
          onClick={() => setToolCatalogOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={toolCatalogOpen}
        >
          <Icon name="wrench" size={15} />
          <span>系统工具</span>
        </button>
      </header>

      <ToolCatalogDialog
        open={toolCatalogOpen}
        mcpServers={status?.mcp_servers ?? []}
        onClose={() => setToolCatalogOpen(false)}
      />

      <div className="conversation" ref={scrollRef} aria-label="聊天记录"
        onScroll={() => setAwayFromBottom(!scroll.isNearBottom())}>
        {chat.state.historyLoading ? (
          <div className="conversation-state">
            <Spin />
            <p>正在加载会话…</p>
          </div>
        ) : !hasMessages && !chat.state.approval && !chat.state.error ? (
          <div className="welcome">
            <div className="welcome-mark">
              <img src="/assets/brand/melonclaw-mark.png" alt="" />
            </div>
            <h1>今天，有什么想一起搞定的？</h1>
            <Prompts
              className="prompt-grid"
              wrap
              items={WELCOME_PROMPTS.map(({ key, icon, label, description }) => ({ key, icon, label, description }))}
              onItemClick={({ data }) => {
                const prompt = WELCOME_PROMPTS.find((item) => item.key === data.key)?.prompt;
                if (prompt) setDraft(prompt);
              }}
            />
          </div>
        ) : (
            <div className="message-list">
            {chat.state.messages.map((message) => (
              <MessageBubble
                key={`${message.id}-${message.role}`}
                message={message}
                userName={userName}
                runStatus={runStatus}
                conversationId={chat.state.conversationId}
                userId={session.userId}
                tenantId={session.tenantId}
                projectId={chat.state.conversationProjectId ?? session.projectId}
              />
            ))}
          </div>
        )}
      <div className="approval-inline" ref={approvalRef}>
        {chat.state.userQuestion ? (
          <UserQuestionPanel
            key={questionKey(chat.state.userQuestion)}
            question={chat.state.userQuestion}
            onSubmit={chat.submitUserInput}
            expired={userQuestionExpired}
          />
        ) : chat.state.approval ? (
          <ApprovalPanel
            key={approvalKey(chat.state.approval)}
            approval={chat.state.approval}
            onSubmit={chat.submitApproval}
          />
        ) : null}
      </div>
      {chat.state.error ? (
        <div className="chat-error" role="alert">
          <span>{chat.state.error}</span>
          <button type="button" onClick={chat.reloadHistory} disabled={chat.state.historyLoading}>重新同步会话</button>
        </div>
      ) : null}
      </div>

      {chat.state.userQuestion || chat.state.approval ? (
        <div className="chat-attention" role="status">
          <Icon name={chat.state.userQuestion ? "message-circle" : "shield-check"} size={16} />
          助手已暂停，等待你的{chat.state.userQuestion ? "回答" : "决定"}
          <button type="button" onClick={() => {
            approvalRef.current?.scrollIntoView({ block: "start" });
            approvalRef.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
          }}>{chat.state.userQuestion ? "查看待处理问题" : "查看待确认操作"}</button>
        </div>
      ) : awayFromBottom ? (
        <button type="button" className="jump-to-latest" onClick={() => scroll.scrollToBottom(true)}>回到最新消息<Icon name="chevron-down" size={15} /></button>
      ) : null}

      <Composer
        onOpenProjectDialog={onOpenProjectDialog}
        value={draft}
        onChange={setDraft}
        onSend={handleSend}
        isRunning={chat.isRunning}
        onStop={() => chat.stopCurrent()}
        disabled={
          session.conversationCreating ||
          chat.state.historyLoading ||
          Boolean(chat.state.approval) ||
          Boolean(chat.state.userQuestion && !userQuestionExpired) ||
          ((chat.isRunning || Boolean(chat.state.error)) &&
            !expiredQuestionCanStartNewMessage)
        }
      />
    </div>
  );
}
