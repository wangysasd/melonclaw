import { Spin } from "antd";
import Bubble from "@ant-design/x/es/bubble";
import Prompts from "@ant-design/x/es/prompts";
import { memo, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "./Icon";
import { Composer } from "./Composer";
import { toolSummary } from "../lib/toolDisplay";
import { ApprovalPanel } from "./ApprovalPanel";
import { UserQuestionPanel } from "./UserQuestionPanel";
import { ImageLightbox } from "./ImageLightbox";
import { Markdown } from "./Markdown";
import { MessageUsage } from "./MessageUsage";
import { ResultProvider } from "./ResultContext";
import { ArtifactWorkspace, ArtifactTrigger, MessageArtifactCards } from "./ArtifactWorkspace";
import { FailureNotice } from "./FailureNotice";
import { StoppedRunNotice } from "./StoppedRunNotice";
import { useUnreadChat } from "../hooks/useUnreadChat";
import { AgentExecution, ExecutionActivityTiming } from "./AgentExecution";
import { useChatStream, type ChatMessage } from "../hooks/useChatStream";
import { buildAgentRun, isTerminalRun } from "../lib/agentRun";
import { attachmentBadge } from "../lib/attachmentFiles";
import { copyText } from "../lib/clipboard";
import { formatMessageTime } from "../lib/format";
import { useUserQuestionExpired } from "../lib/userQuestionExpiry";
import { useSession } from "../state/session";
import { attachmentContentUrl } from "../api/client";
import type { PendingApproval, SkillOption, UserQuestionRequest } from "../types/api";

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

const WELCOME_PROMPTS = [
  {
    key: "capabilities",
    icon: <Icon name="brain" size={18} className="prompt-icon" />,
    label: "了解助手能力",
    description: "查看可以协助处理的研究任务",
    prompt: "请介绍你能协助我完成哪些研究任务，并举例说明各自的适用场景。",
  },
  {
    key: "industry-outlook",
    icon: <Icon name="scan-search" size={18} className="prompt-icon" />,
    label: "查看行业观点",
    description: "查询行业或赛道的最新周度观点",
    prompt: "请查询【行业/赛道】最新一条周度观点，区分行业观点与赛道观点，保留原文并注明日期。",
  },
  {
    key: "research-reports",
    icon: <Icon name="book-open" size={18} className="prompt-icon" />,
    label: "检索研究报告",
    description: "按公司、行业或方向查找最新报告",
    prompt: "请按【公司/行业/研究方向】检索最新研究报告，列出标题、发布时间、摘要和评级信息，完整保留返回内容。",
  },
  {
    key: "market-review",
    icon: <Icon name="calendar-days" size={18} className="prompt-icon" />,
    label: "复盘近期市场",
    description: "回顾最近交易日或一周的A股行情",
    prompt: "请复盘最近一个交易日的A股市场，梳理主要指数、行业板块表现和市场驱动因素，并总结后续观察要点。",
  },
  {
    key: "financial-model",
    icon: <Icon name="calculator" size={18} className="prompt-icon" />,
    label: "查询公司财务数据",
    description: "查看财务三表、指标或预测数据",
    prompt: "请查询【公司】的营业收入、归母净利润等指标，或展示利润表、资产负债表、现金流量表；注明年份并区分实际值与预测值。",
  },
  {
    key: "article-search",
    icon: <Icon name="search" size={18} className="prompt-icon" />,
    label: "搜索研究文章",
    description: "查找主题文章与发布时间、摘要",
    prompt: "请检索【主题】相关研究文章，整理标题、发布日期和正文摘要；若没有检索到，请明确说明。",
  },
  {
    key: "performance-review",
    icon: <Icon name="list-checks" size={18} className="prompt-icon" />,
    label: "梳理业绩表现",
    description: "提炼财务指标、经营亮点与风险",
    prompt: "请点评【公司】最新一期业绩，提取核心财务指标，分析盈利驱动、经营亮点和潜在风险。",
  },
  {
    key: "core-assumptions",
    icon: <Icon name="brain" size={18} className="prompt-icon" />,
    label: "拆解经营假设",
    description: "分析业务贡献与关键变量敏感性",
    prompt: "请拆解【公司】的业务线贡献和关键经营假设，说明各变量变化对收入、利润预测的敏感性。",
  },
  {
    key: "earnings-forecast",
    icon: <Icon name="calculator" size={18} className="prompt-icon" />,
    label: "分析盈利预测",
    description: "查看预测指标、业务贡献和变动原因",
    prompt: "请查询【公司】未来几年的营收、净利润和每股收益预测，拆分业务贡献，解释预测变动并与同行对比。",
  },
  {
    key: "stock-valuation",
    icon: <Icon name="scan-search" size={18} className="prompt-icon" />,
    label: "分析个股估值",
    description: "结合历史分位、同业比较与基本面",
    prompt: "请分析【公司】的PE、PB、PS指标、近1年和3年历史分位及同业估值差异，梳理估值支撑逻辑与风险。",
  },
  {
    key: "industry-research",
    icon: <Icon name="blocks" size={18} className="prompt-icon" />,
    label: "研究行业赛道",
    description: "梳理产业链、供需变化与竞争格局",
    prompt: "请研究【行业/赛道】的产业链、供需变化、竞争格局和增长驱动，结合A股行业配置视角列出关键观察指标与风险。",
  },
  {
    key: "macro-assets",
    icon: <Icon name="globe-2" size={18} className="prompt-icon" />,
    label: "分析宏观与资产",
    description: "理解政策、利率汇率和跨资产传导",
    prompt: "请分析【宏观主题】对经济周期、货币政策、利率、汇率、债券及海内外市场的传导关系，梳理跨资产逻辑并标注不确定性。",
  },
];

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
          const url = attachmentContentUrl(attachment.attachment_id, {
            userId,
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
  const metaLabel = message.role === "user" ? userName : __MELONCLAW_NAME__;
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
      <div className={`avatar ${message.role === "user" ? "user-avatar" : "assistant-avatar"}`}>
        <img
          src={
            message.role === "user"
              ? "/assets/brand/melon.png"
              : "/assets/brand/melonclaw-mark.png"
          }
          alt={message.role === "user" ? userName : __MELONCLAW_NAME__}
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
        <div className="message-body">
          {run?.status === "running" && run.liveAnswer ? <span className="assistant-live-label">生成中</span> : null}
          {message.role === "user" || displayContent ? (
            <Bubble
              placement={message.role === "user" ? "end" : "start"}
              variant={message.role === "assistant" ? "borderless" : "filled"}
              content={message.role === "assistant"
                ? <Markdown source={renderedContent} streaming={message.status === "streaming"} fileCardsAtEnd={message.status === "completed"} deliveredFiles={message.artifacts} />
                : message.content}
            />
          ) : null}
        </div>
        {run ? <ExecutionActivityTiming run={run} /> : null}
        {message.role === "assistant" && message.status === "completed" ? <MessageArtifactCards artifacts={message.artifacts} /> : null}
        {run && message.status !== "streaming" ? <FailureNotice message={message} run={run} onSync={onSync} syncDisabled={syncDisabled} /> : null}
        {message.status === "cancelled" && run ? <StoppedRunNotice run={run} events={message.events} onSync={onSync} syncDisabled={syncDisabled} /> : null}
        {run && isTerminalRun(run.status) ? <MessageUsage events={message.events} /> : null}
        <MessageFooter message={message} />
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

  const chat = useChatStream({ scroll });
  const chatContextKey = `${session.userId}:${session.projectId}:${session.conversationId ?? "new"}`;
  const unread = useUnreadChat(chat.state.messages, chatContextKey, awayFromBottom, chat.state.historyLoading);
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
    // 首次发送或附件暂存会先拿到会话 ID，此时输入框仍属于同一张空白页。
    if (session.conversationId && session.conversationId === session.draftConversationId) return;
    setDraft("");
    chat.clearRestoreDraft();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.state.conversationId, session.conversationId, session.draftConversationId]);
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
    <ArtifactWorkspace key={`${session.userId}:${session.conversationId ?? "new"}:${conversationProjectId ?? ""}`} userId={session.userId} conversationId={session.conversationId} projectId={conversationProjectId ?? null} projectName={projectName} messages={chatMatchesSelection ? chat.state.messages : []} onLocateMessage={chat.loadMessage}>
    <div className="chat-view">
      <header className="topbar">
        <div className="topbar-leading">
          <button
            type="button"
            className="icon-button sidebar-reopen"
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
        ) : !hasMessages && !chat.state.approval && !chat.state.error ? (
          <div className="welcome">
            <div className="welcome-mark">
              <img src="/assets/brand/melonclaw-word.png" alt="MelonClaw" />
            </div>
            <h1>请选择您需要的研究服务</h1>
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
      {awayFromBottom ? (
        <button type="button" className={`jump-to-latest${unread.hasUnread ? " has-unread" : ""}`} onClick={() => { unread.acknowledge(); scroll.scrollToBottom(true); }}>
          {unread.hasUnread ? "有新内容 · 回到最新消息" : "回到最新消息"}<Icon name="chevron-down" size={16} />
        </button>
      ) : null}

      <Composer
        onOpenProjectDialog={onOpenProjectDialog}
        onOpenModelSettings={onOpenModelSettings}
        initialSkill={initialSkill}
        onInitialSkillApplied={onInitialSkillApplied}
        value={draft}
        onChange={setDraft}
        onSend={handleSend}
        isRunning={chat.isRunning}
        onStop={() => chat.stopCurrent()}
        pendingInteraction={chat.state.userQuestion && !userQuestionExpired ? "question" : chat.state.approval ? "approval" : null}
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
    </ArtifactWorkspace>
  );
}
