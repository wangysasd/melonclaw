import { useAssistantName } from "../state/assistantName";
import { Spin, Tooltip } from "antd";
import Bubble from "@ant-design/x/es/bubble";
import { memo, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "./Icon";
import { Composer } from "./Composer";
import { toolSummary } from "../lib/toolDisplay";
import { ApprovalPanel } from "./ApprovalPanel";
import { UserQuestionPanel } from "./UserQuestionPanel";
import { AuthorizedImage, AuthorizedLightbox, downloadAuthorized } from "./AuthorizedImage";
import { Markdown } from "./Markdown";
import { KnowledgeCitationProvider, KnowledgeSourceAllow, useKnowledgeCitation } from "./KnowledgeCitationProvider";
import { knowledgeSourceLabel, messageKnowledgeSources } from "../lib/knowledgeSources";
import { MessageUsage } from "./MessageUsage";
import { ResultProvider } from "./ResultContext";
import { ArtifactWorkspace, ArtifactTrigger, MessageArtifactCards } from "./ArtifactWorkspace";
import { FailureNotice } from "./FailureNotice";
import { ReadAloudControls } from "./ReadAloudControls";
import { StoppedRunNotice } from "./StoppedRunNotice";
import { useUnreadChat } from "../hooks/useUnreadChat";
import { AgentExecution, ExecutionElapsedTiming } from "./AgentExecution";
import { useChatStream } from "../hooks/useChatStream";
import type { ChatMessage } from "../state/chatStream";
import { buildAgentRun, isTerminalRun } from "../lib/agentRun";
import { attachmentBadge } from "../lib/attachmentFiles";
import { environmentChatDisplay } from "../lib/environmentChat";
import { copyText } from "../lib/clipboard";
import { canReadAloud, stopReadAloud } from "../lib/readAloud";
import { avatarStyle, formatMessageTime, userInitials } from "../lib/format";
import { useUserQuestionExpired } from "../lib/userQuestionExpiry";
import { useSession } from "../state/session";
import { attachmentContentUrl } from "../api/client";
import type { AttachmentSummary, PendingApproval, SkillOption, UserQuestionRequest } from "../types/api";

/** 审批面板重挂载 key：interrupt ID 组合变化时重置面板内部表单状态。 */
function approvalKey(approval: PendingApproval): string {
  const binding = `${approval.approval_batch_id}:${approval.assistant_message_id}`;
  return `${binding}:${approval.interrupts.map((item) => item.id).join(",")}`;
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

function MessageAttachments({
  attachments,
  userId,
  projectId,
  conversationId,
}: {
  attachments: NonNullable<ChatMessage["attachments"]>;
  userId: string;
  projectId: string;
  conversationId: string | null;
}) {
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);
  if (attachments.length === 0) return null;
  return (
    <>
      <div className="message-attachments" aria-label="消息附件">
        {attachments.map((attachment) => {
          const access = {
            userId,
            projectId: projectId || null,
            conversationId: projectId ? null : conversationId,
          };
          const url = attachmentContentUrl(attachment.attachment_id, access);
          const previewUrl = attachment.media_type === "image/tiff"
            ? attachmentContentUrl(attachment.attachment_id, access, true) : url;
          const isImage = attachment.kind === "image";
          return (
            <div className="message-attachment" key={attachment.attachment_id}>
              {isImage ? (
                <button
                  type="button"
                  className="message-attachment-thumb"
                  aria-label={`预览 ${attachment.file_name}`}
                  onClick={() => setPreview({ url: previewUrl, name: attachment.file_name })}
                >
                  <AuthorizedImage url={previewUrl} alt={attachment.file_name} />
                </button>
              ) : (
                <span className="attachment-file-icon" aria-hidden="true">
                  <Icon name="file-text" size={18} />
                  <span className="attachment-badge">{attachmentBadge(attachment.file_name)}</span>
                </span>
              )}
              <div className="message-attachment-info">
                <button type="button" className="message-attachment-download" onClick={() => void downloadAuthorized(url, attachment.file_name)}>{attachment.file_name}</button>
                <span>{isImage ? "图片" : attachment.parse_status === "failed" ? "解析失败" : "文档"}</span>
              </div>
            </div>
          );
        })}
      </div>
      {preview ? (
        <AuthorizedLightbox url={preview.url} alt={preview.name} onClose={() => setPreview(null)} />
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
      await copyText(message.role === "user" ? environmentChatDisplay(message.content) : message.content);
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

function MessageFooter({
  message,
  conversationId,
  userId,
}: {
  message: ChatMessage;
  conversationId: string | null;
  userId: string;
}) {
  const copyable = message.role !== "user" && Boolean(message.content) && message.status !== "streaming";
  const readable = canReadAloud(message) && Boolean(conversationId);
  if (!copyable && !readable) return null;
  return (
    <div className="message-actions">
      {copyable ? <CopyButton message={message} /> : null}
      {readable ? (
        <ReadAloudControls message={message} conversationId={conversationId ?? ""} userId={userId} />
      ) : null}
    </div>
  );
}

const MessageBubble = memo(function MessageBubble({
  message,
  userName,
  waitingFor,
  onSync,
  syncDisabled,
  conversationId,
  userId,
  projectId,
}: {
  message: ChatMessage;
  userName: string;
  waitingFor?: "question" | "approval";
  onSync: () => void;
  syncDisabled: boolean;
  conversationId: string | null;
  userId: string;
  projectId: string;
}) {
  const assistantName = useAssistantName();
  const citation = useKnowledgeCitation();
  const sources = useMemo(
    () => message.role === "assistant" ? messageKnowledgeSources(message, window.location.origin) : null,
    [message],
  );
  const metaLabel = message.role === "user" ? userName : assistantName;
  const metaDetail =
    message.role === "user"
      ? formatMessageTime(message.timestamp ?? null)
      : "";
  const run = useMemo(
    () =>
      message.role === "assistant" ? buildAgentRun(message, conversationId) : null,
    [message, conversationId],
  );
  // 生成中的最后一个无工具 step 只作临时预览；终态仍使用服务端的最终正文。
  const displayContent = run ? run.finalAnswer ?? run.liveAnswer ?? "" : message.content;
  const deferredContent = useDeferredValue(displayContent);
  const renderedContent = run?.status === "running" ? displayContent : deferredContent;

  return (
    <ResultProvider userId={userId} conversationId={conversationId ?? ""} projectId={projectId || null} messageId={message.id}>
    <article className={`message ${message.role}`} id={`message-${message.id}`} tabIndex={-1}>
      <div
        className={`avatar ${message.role === "user" ? "user-avatar" : "assistant-avatar"}`}
        style={message.role === "user" ? avatarStyle(userId) : undefined}
        role={message.role === "user" ? "img" : undefined}
        aria-label={message.role === "user" ? userName : undefined}
      >
        {message.role === "user" ? userInitials(userName, userId) : (
          <img
            src={__MELONCLAW_IS_RMS_BRAND__ ? "/assets/brand/melonclaw-mark-rms.png" : "/assets/brand/melonclaw-mark-transparent.webp"}
            alt={assistantName}
          />
        )}
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
            waitingFor={waitingFor}
          />
        ) : null}
        {message.approvalReceipts?.map((receipt) => <div className="approval-receipt" role="status" key={receipt.batchId}>
          {receipt.actions.map((action, index) => <p key={index}>
            {toolSummary(action.name)} · {({ approve: "已允许本次", reject: "已拒绝", edit: "已提交修改参数", respond: "已提供结果" })[action.decision]}
          </p>)}
          <span>决定已接收，后续执行结果见执行过程。</span>
        </div>)}
        <MessageAttachments
          attachments={message.attachments ?? []}
          userId={userId}
          projectId={projectId}
          conversationId={conversationId}
        />
        <KnowledgeSourceAllow allow={message.role === "assistant" && sources?.enforce ? sources.keys : null}>
        <div className="message-body">
          {run?.status === "running" && run.liveAnswer ? <span className="assistant-live-label">生成中</span> : null}
          {message.role === "user" || displayContent ? (
            <Bubble
              placement={message.role === "user" ? "end" : "start"}
              variant={message.role === "assistant" ? "borderless" : "filled"}
              content={message.role === "assistant"
                ? <Markdown source={renderedContent} streaming={run?.status === "running"} fileCardsAtEnd={message.status === "completed"} deliveredFiles={message.artifacts} />
                : environmentChatDisplay(message.content)}
            />
          ) : null}
        </div>
        {sources?.listed.length ? <ul className="knowledge-answer-sources" aria-label="知识库来源">
          {sources.listed.map((card) => <li key={card.source_url}>
            <button type="button" onClick={() => citation?.openFromHref(card.source_url)}>{knowledgeSourceLabel(card)}</button>
          </li>)}
        </ul> : null}
        </KnowledgeSourceAllow>
        {run ? <ExecutionElapsedTiming run={run} /> : null}
        {message.role === "assistant" && message.status === "completed" ? <MessageArtifactCards artifacts={message.artifacts} /> : null}
        {run && message.status !== "streaming" ? <FailureNotice message={message} run={run} onSync={onSync} syncDisabled={syncDisabled} /> : null}
        {message.status === "cancelled" && run ? <StoppedRunNotice run={run} events={message.events} onSync={onSync} syncDisabled={syncDisabled} /> : null}
        {run && isTerminalRun(run.status) ? <MessageUsage events={message.events} /> : null}
        <MessageFooter message={message} conversationId={conversationId} userId={userId} />
      </div>
    </article>
    </ResultProvider>
  );
});

/** 聊天主视图：消息与审批共用阅读流，底部保留输入区与状态提醒。 */
interface ChatViewProps {
  active?: boolean;
  onOpenProjectDialog?: () => void;
  /** 打开拓展页的模型供应商 TAB。 */
  onOpenModelSettings?: () => void;
  initialSkill?: SkillOption | null;
  onInitialSkillApplied?: () => void;
}

export function ChatView({
  active = true,
  onOpenProjectDialog,
  onOpenModelSettings,
  initialSkill = null,
  onInitialSkillApplied,
}: ChatViewProps = {}) {
  const session = useSession();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const scrollFrame = useRef<number | null>(null);
  const visibility = useRef({ active, awayFromBottom });
  visibility.current = { active, awayFromBottom };
  const readingPosition = useRef(0);
  const approvalRef = useRef<HTMLDivElement>(null);

  const scroll = useMemo(
    () => ({
      isNearBottom: () => {
        if (!visibility.current.active) return !visibility.current.awayFromBottom;
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
          if (!element || !visibility.current.active) return;
          element.scrollTo({
            top: element.scrollHeight,
            behavior: smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "auto",
          });
        });
      },
    }),
    [],
  );

  const [queueDrag, setQueueDrag] = useState<{ source: string; target?: string } | null>(null);
  const chat = useChatStream({ scroll, active });
  const chatContextKey = `${session.userId}:${session.projectId}:${session.conversationId ?? "new"}`;
  useEffect(() => () => {
    stopReadAloud();
  }, [chatContextKey]);
  useEffect(() => {
    if (!active) stopReadAloud();
  }, [active]);
  const unread = useUnreadChat(chat.state.messages, chatContextKey, awayFromBottom, chat.state.historyLoading);
  const userQuestionExpired = useUserQuestionExpired(
    chat.state.userQuestion?.expires_at,
  );

  useEffect(() => () => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
  }, []);
  // 切换会话即换一张“白纸”：旧会话没发出去的草稿不能带到新会话，否则新建
  // 会话看起来和没点一样（同样的欢迎页 + 同样的输入框文字）。失败回填走
  // restoreDraft（只在输入框为空时生效），切会话时旧会话的回填已无意义，一并丢弃。
  useEffect(() => {
    // 首次发送或附件暂存会先拿到会话 ID，此时输入框仍属于同一张空白页。
    if (session.conversationId && session.conversationId === session.draftConversationId) return;
    setDraft("");
    chat.clearRestoreDraft();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatContextKey]);
  useEffect(() => {
    setAwayFromBottom(false);
    readingPosition.current = 0;
  }, [chatContextKey]);
  useEffect(() => {
    if (!chat.state.historyLoading) scroll.scrollToBottom();
  }, [chat.state.conversationId, chat.state.historyLoading, scroll]);

  useEffect(() => {
    if (!active) return;
    if (visibility.current.awayFromBottom) {
      if (scrollRef.current) scrollRef.current.scrollTop = readingPosition.current;
    } else scroll.scrollToBottom();
  }, [active, scroll]);

  // 失败回滚：仅当输入框为空时回填草稿（对齐旧 restoreDraft）。
  useEffect(() => {
    if (!chat.state.restoreDraft) return;
    const failedDraft = chat.state.restoreDraft;
    setDraft((current) => current || failedDraft);
    chat.clearRestoreDraft();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.state.restoreDraft]);

  const handleSend = (
    value: string,
    skillId?: string | null,
    attachmentIds?: string[],
    onAccepted?: () => void,
    knowledgeBaseIds?: string[],
    imageAttachmentIds?: string[],
    onAttachmentsPrepared?: (attachments: AttachmentSummary[]) => void,
  ) => {
    stopReadAloud();
    void chat.sendMessage(
      value,
      skillId,
      onAccepted,
      attachmentIds,
      knowledgeBaseIds,
      () => setDraft((current) => current === value ? "" : current),
      imageAttachmentIds,
      onAttachmentsPrepared,
    );
  };

  const hasMessages = chat.state.messages.length > 0;
  const showWelcome = !hasMessages && !chat.state.historyLoading &&
    !chat.state.approval && !chat.state.userQuestion && !chat.state.error;
  const currentUser = session.users.find((user) => user.user_id === session.userId);
  const userName = currentUser?.display_name || currentUser?.username || session.userId || "用户";
  const currentConversation = session.conversationId
    ? session.conversations.find((item) => item.id === session.conversationId)
      ?? session.recents.find((item) => item.id === session.conversationId)
      ?? session.optimisticConversations.find((item) => item.id === session.conversationId)
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
    <KnowledgeCitationProvider>
    <ArtifactWorkspace key={`${session.userId}:${session.conversationId ?? "new"}:${conversationProjectId ?? ""}`} userId={session.userId} conversationId={session.conversationId} projectId={conversationProjectId ?? null} projectName={projectName} messages={chatMatchesSelection ? chat.state.messages : []} onLocateMessage={chat.loadMessage}>
    <div className={showWelcome ? "chat-view is-empty" : "chat-view"}>
      <header className="topbar">
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button sidebar-reopen sidebar-panel-toggle"
            onClick={() =>
              window.dispatchEvent(new Event("melonclaw:open-sidebar"))
            }
            aria-label="展开侧栏"
            title="展开侧栏"
          >
            <Icon name="panel-left" size={18} />
          </button>
          <div className="topbar-session-name">
            {topbarTitle}
          </div>
        </div>
        <div className="topbar-actions"><ArtifactTrigger /></div>
      </header>

      <div className="conversation" ref={scrollRef} aria-label="聊天记录"
        onScroll={() => {
          if (!active) return;
          readingPosition.current = scrollRef.current?.scrollTop ?? 0;
          setAwayFromBottom(!scroll.isNearBottom());
        }}>
        {chat.state.historyLoading ? (
          <div className="conversation-state">
            <Spin />
            <p>正在加载会话…</p>
          </div>
        ) : showWelcome ? (
          <div className="welcome">
            <div className="welcome-mark">
              <img
                src={__MELONCLAW_IS_RMS_BRAND__ ? "/assets/brand/melonclaw-word-rms.png" : "/assets/brand/melonclaw-word-transparent.webp"}
                alt={__MELONCLAW_IS_RMS_BRAND__ ? "RMS" : "MelonClaw"}
              />
            </div>
          </div>
        ) : (
            <div className="message-list">
            {chat.state.messages.map((message) => (
              <MessageBubble
                key={`${message.id}-${message.role}`}
                message={message}
                userName={userName}
                waitingFor={chat.state.userQuestion?.assistant_message_id === message.id ? "question" : chat.state.approval?.assistant_message_id === message.id ? "approval" : undefined}
                onSync={chat.reloadHistory}
                syncDisabled={chat.isRunning || chat.state.historyLoading}
                conversationId={chat.state.conversationId}
                userId={session.userId}
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
          {chat.state.messages.at(-1)?.errorCode === "model_execution_failed" && onOpenModelSettings ? <button type="button" onClick={onOpenModelSettings}>检查模型配置</button> : null}
          <button type="button" onClick={chat.reloadHistory} disabled={chat.isRunning || chat.state.historyLoading}>重新同步会话</button>
        </div>
      ) : null}
      </div>

      {chat.state.userQuestion || chat.state.approval ? (
        <div className="chat-attention" role="status">
          <Icon name={chat.state.userQuestion ? "message-circle" : "shield-check"} size={16} />
          {chat.state.userQuestion
            ? userQuestionExpired ? "问题已过期，可以发送新消息继续" : "发送已暂停，请先回答问题或让 AI 自己决定"
            : "发送已暂停，请先允许或拒绝待确认操作"}
          <button type="button" onClick={() => {
            approvalRef.current?.scrollIntoView({ block: "start" });
            approvalRef.current?.querySelector<HTMLElement>("section")?.focus({ preventScroll: true });
          }}>{chat.state.userQuestion ? "查看待处理问题" : "查看待确认操作"}</button>
        </div>
      ) : null}
      {hasMessages && awayFromBottom ? (
        <button type="button" className={`jump-to-latest${unread.hasUnread ? " has-unread" : ""}`} onClick={() => { unread.acknowledge(); scroll.scrollToBottom(true); }}>
          {unread.hasUnread ? "有新内容 · 回到最新消息" : "回到最新消息"}<Icon name="chevron-down" size={16} />
        </button>
      ) : null}

      <Composer
        active={active}
        queuePanel={chat.queue && (chat.queue.items.length > 0 || chat.queue.blocked) ? (
          <section className="chat-send-queue" aria-label="待发送消息">
            {chat.queue.blocked ? (
              <div className="chat-send-queue-row">
                <span role="status">{chat.queue.blocked.reason}{chat.queue.items.length ? "，待发送消息已保留" : ""}</span>
                {chat.queue.blocked.messageId || (chat.queue.blocked.attachmentPreparationFailed && chat.queue.items.length) ? (
                  <button type="button" disabled={chat.queue.checking || chat.isRunning} onClick={() => void chat.sendQueued()}>
                    {chat.queue.blocked.attachmentPreparationFailed
                      ? "重新识别并发送"
                      : chat.queue.items.length ? "核实并发送" : "核实上一轮"}
                  </button>
                ) : null}
              </div>
            ) : null}
            <ol>
              {chat.queue.items.map((item, index, items) => (
                <li
                  className={`chat-send-queue-row${queueDrag?.target === item.id ? " is-drop-target" : ""}`}
                  key={item.id}
                  onDragOver={(event) => {
                    if (!queueDrag || queueDrag.source === item.id || chat.queue?.sending === item.id) return;
                    event.preventDefault();
                    event.dataTransfer.dropEffect = "move";
                    if (queueDrag.target !== item.id) setQueueDrag({ source: queueDrag.source, target: item.id });
                  }}
                  onDrop={(event) => {
                    if (!queueDrag) return;
                    event.preventDefault();
                    chat.moveQueued(queueDrag.source, item.id);
                    setQueueDrag(null);
                  }}
                >
                  <button
                    type="button"
                    className="chat-send-queue-handle"
                    aria-label={`调整第 ${index + 1} 条待发送消息的顺序`}
                    title="拖动排序，或按上下方向键移动"
                    disabled={items.length < 2 || chat.queue?.sending === item.id}
                    draggable={items.length > 1 && chat.queue?.sending !== item.id}
                    onDragStart={(event) => {
                      event.dataTransfer.effectAllowed = "move";
                      event.dataTransfer.setData("text/plain", item.id);
                      setQueueDrag({ source: item.id });
                    }}
                    onDragEnd={() => setQueueDrag(null)}
                    onKeyDown={(event) => {
                      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
                      event.preventDefault();
                      const target = items[index + (event.key === "ArrowUp" ? -1 : 1)];
                      if (target) chat.moveQueued(item.id, target.id);
                    }}
                  >
                    <span aria-hidden="true">⠿</span>
                  </button>
                  <Tooltip
                    title={item.preview}
                    placement="top"
                    styles={{ container: { fontSize: "var(--text-md)", whiteSpace: "pre-wrap", overflowWrap: "anywhere" } }}
                  >
                    <span>{item.preview || `${item.attachmentIds.length} 个附件`}</span>
                  </Tooltip>
                  {item.preview ? <button type="button" onClick={() => void copyText(item.preview).catch(() => undefined)}>复制</button> : null}
                  <button type="button" disabled={chat.queue?.sending === item.id} aria-label="移除待发送消息" title="移除" onClick={() => chat.removeQueued(item.id)}><Icon name="trash-2" size={14} /></button>
                </li>
              ))}
            </ol>
          </section>
        ) : null}
        queueEnabled={Boolean(session.conversationId)}
        hasQueuedMessages={Boolean(chat.queue?.items.length || chat.queue?.blocked)}
        onOpenProjectDialog={onOpenProjectDialog}
        onOpenModelSettings={onOpenModelSettings}
        initialSkill={initialSkill}
        onInitialSkillApplied={onInitialSkillApplied}
        value={draft}
        onChange={setDraft}
        onSend={handleSend}
        isRunning={chat.isRunning}
        preparingAttachments={chat.preparingAttachments}
        onStop={() => {
          stopReadAloud();
          chat.stopCurrent();
        }}
        pendingInteraction={chat.state.userQuestion && !userQuestionExpired ? "question" : chat.state.approval ? "approval" : null}
        disabled={session.conversationCreating || chat.state.historyLoading ||
          (Boolean(chat.state.error) && !chat.queue?.blocked)}
      />
    </div>
    </ArtifactWorkspace>
    </KnowledgeCitationProvider>
  );
}
