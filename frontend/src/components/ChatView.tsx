import { Spin } from "antd";
import { memo, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import { Icon, type IconName } from "./Icon";
import { Composer } from "./Composer";
import { ApprovalPanel } from "./ApprovalPanel";
import { Markdown } from "./Markdown";
import { ToolTimeline } from "./ToolTimeline";
import { useChatStream, type ChatMessage } from "../hooks/useChatStream";
import { useServiceStatus } from "../hooks/useServiceStatus";
import { copyText } from "../lib/clipboard";
import { formatMessageTime } from "../lib/format";
import { useSession, type RunStatus } from "../state/session";
import type { PendingApproval } from "../types/api";

const STATUS_LABELS: Record<string, string> = {
  pending: "处理中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "等待确认",
};

const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  starting: "连接中",
  ready: "已就绪",
  selecting_tools: "正在工具筛选",
  processing: "处理中",
  waiting: "等待确认",
  failed: "失败",
};

const RUN_STATUS_ICONS: Record<RunStatus, IconName> = {
  starting: "loader-circle",
  ready: "circle-check",
  selecting_tools: "loader-circle",
  processing: "loader-circle",
  waiting: "shield-check",
  failed: "circle-alert",
};

/** 审批面板重挂载 key：interrupt ID 组合变化时重置面板内部表单状态。 */
function approvalKey(approval: PendingApproval): string {
  if (Array.isArray(approval.interrupts) && approval.interrupts.length > 0) {
    return approval.interrupts.map((item) => item.id).join(",");
  }
  return approval.id ?? "single";
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
}: {
  message: ChatMessage;
  userName: string;
  runStatus: RunStatus;
}) {
  const status =
    message.status === "streaming"
      ? "处理中"
      : (STATUS_LABELS[message.status ?? ""] ?? "");
  const metaLabel = message.role === "user" ? userName : "MelonClaw";
  const metaDetail =
    message.role === "user"
      ? formatMessageTime(message.timestamp ?? null)
      : status;
  const renderedContent = useDeferredValue(message.content);

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
          {message.role === "assistant" && message.model?.model ? (
            <span className="message-model">{message.model.model}</span>
          ) : null}
          {metaDetail ? <span className="message-time">{metaDetail}</span> : null}
        </div>
        {message.role === "assistant" ? <ToolTimeline events={message.events} messageStatus={message.status} /> : null}
        <div className="message-body">
          {message.role === "assistant" ? <Markdown source={renderedContent} /> : message.content}
        </div>
        {message.status === "streaming" && !message.content && message.events.length === 0 ? (
          <div className="message-progress" role="status"><Icon name="loader-circle" size={15} className="mc-icon-spin" />{runStatus === "selecting_tools" ? "正在工具筛选…" : "正在准备回复…"}</div>
        ) : null}
        {message.status === "failed" || message.status === "cancelled" ? (
          <p className="message-notice">{message.status === "failed" ? "本次回复未完成，当前显示已接收的内容。" : "本次回复已中止。"}</p>
        ) : null}
        <MessageFooter message={message} />
      </div>
    </article>
  );
});

/** 聊天主视图：消息与审批共用阅读流，底部保留输入区与状态提醒。 */
export function ChatView() {
  const session = useSession();
  const { runStatus } = useServiceStatus();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState("");
  const [awayFromBottom, setAwayFromBottom] = useState(false);
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
  useEffect(() => () => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
  }, []);
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

  const handleSend = (value: string) => {
    void chat.sendMessage(value, () => setDraft((current) => current === value ? "" : current));
  };

  const hasMessages = chat.state.messages.length > 0;
  const projectName =
    session.projects.find((project) => project.id === session.projectId)?.name ??
    "未选择项目";
  const userName =
    session.users.find((user) => user.user_id === session.userId)?.display_name ??
    (session.userId || "用户");
  const statusIcon = RUN_STATUS_ICONS[runStatus];

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
          <div>
            <div className="topbar-brand">MelonClaw</div>
            <div className="topbar-context">
              瓜爪助手 · <span>{projectName}</span>
            </div>
          </div>
        </div>
        <div className="topbar-meta">
          <span className="session-label">
            {chat.state.conversationTitle || "准备开始"}
          </span>
          <span className="run-status-pill" data-status={runStatus}>
            <Icon
              name={statusIcon}
              size={15}
              className={
                runStatus === "starting" || runStatus === "selecting_tools" || runStatus === "processing"
                  ? "mc-icon-spin"
                  : undefined
              }
            />
            <span>{RUN_STATUS_LABELS[runStatus]}</span>
          </span>
        </div>
      </header>

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
            <div className="welcome-kicker">你好，我是 MelonClaw</div>
            <h1>今天，有什么想一起搞定的？</h1>
            <p className="welcome-copy">查资料、理思路、做计划，瓜爪来帮你。</p>
            <div className="prompt-grid">
              {WELCOME_PROMPTS.map(({ key, icon, label, description, prompt }) => (
                <button
                  key={key}
                  type="button"
                  className="prompt-card"
                  onClick={() => setDraft(prompt)}
                >
                  {icon}
                  <span className="prompt-copy">
                    <strong>{label}</strong>
                    <small>{description}</small>
                  </span>
                  <Icon name="chevron-right" size={17} className="prompt-arrow" />
                </button>
              ))}
            </div>
          </div>
        ) : (
            <div className="message-list">
            {chat.state.messages.map((message) => (
              <MessageBubble
                key={`${message.id}-${message.role}`}
                message={message}
                userName={userName}
                runStatus={runStatus}
              />
            ))}
          </div>
        )}
      <div className="approval-inline" ref={approvalRef}>
        {chat.state.approval ? (
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

      {chat.state.approval ? (
        <div className="chat-attention" role="status">
          <Icon name="shield-check" size={16} />助手已暂停，等待你的决定
          <button type="button" onClick={() => {
            approvalRef.current?.scrollIntoView({ block: "start" });
            approvalRef.current?.querySelector<HTMLButtonElement>("button")?.focus({ preventScroll: true });
          }}>查看待确认操作</button>
        </div>
      ) : awayFromBottom ? (
        <button type="button" className="jump-to-latest" onClick={() => scroll.scrollToBottom(true)}>回到最新消息<Icon name="chevron-down" size={15} /></button>
      ) : null}

      <Composer
        value={draft}
        onChange={setDraft}
        onSend={handleSend}
        disabled={session.busy || session.conversationCreating || chat.state.historyLoading || Boolean(chat.state.approval) || Boolean(chat.state.error)}
      />
    </div>
  );
}
