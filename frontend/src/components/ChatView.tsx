import { useAssistantName } from "../state/assistantName";
import { Spin } from "antd";
import Bubble from "@ant-design/x/es/bubble";
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
import { environmentChatDisplay } from "../lib/environmentChat";
import { copyText } from "../lib/clipboard";
import { avatarStyle, formatMessageTime, userInitials } from "../lib/format";
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

const WELCOME_CATEGORIES = [
  { key: "company", icon: "building-2", label: "个股与公司研究" },
  { key: "industry", icon: "blocks", label: "行业与策略研究" },
  { key: "experts", icon: "users", label: "专家观点" },
  { key: "reports", icon: "file-text", label: "研究资料与报告" },
  { key: "review", icon: "calendar-days", label: "业绩与市场复盘" },
  { key: "bond", icon: "shield-check", label: "债券与信用" },
  { key: "multi-asset", icon: "globe-2", label: "宏观、资金与多资产" },
] as const;

const WELCOME_PROMPTS = [
  { category: "company", key: "company-assumptions", label: "拆解公司经营假设", description: "业务贡献、关键变量与敏感性", prompt: "查询【公司】的核心假设变量、业务线贡献及其对收入和利润预测的敏感性。" },
  { category: "company", key: "company-earnings", label: "查看盈利预测", description: "预测指标、变动原因与同行比较", prompt: "查询【公司】未来几年的营收、净利润和每股收益预测，解释预测变化并与同行比较。" },
  { category: "company", key: "company-valuation", label: "分析公司估值", description: "估值指标、历史分位与估值支撑", prompt: "分析【公司】的 PE、PB、PS、近三年估值分位及同业差异，梳理估值支撑逻辑与风险。" },
  { category: "company", key: "company-market", label: "查询个股行情", description: "查看近期日行情与区间表现", prompt: "拉【股票代码/名称】最近一个月日行情，并总结区间高低点和近期表现。" },
  { category: "company", key: "company-profile", label: "查询公司信息", description: "了解企业工商登记与主体信息", prompt: "查询【企业名称】工商信息，包括成立时间、注册资本、法定代表人和实际控制人。" },
  { category: "company", key: "company-model", label: "查询公司财务数据", description: "查看财务指标并区分实际值与预测值", prompt: "查询【公司】的营业收入、归母净利润等主要财务指标，注明年份并区分实际值与预测值。" },
  { category: "company", key: "company-peers", label: "对比同行公司", description: "比较业务结构、盈利能力与估值差异", prompt: "对比【公司A】与【公司B】的业务结构、收入增长、盈利能力和估值水平，统一数据期间与口径，注明来源并说明主要差异。" },
  { category: "company", key: "company-cash-flow", label: "分析现金流质量", description: "观察利润兑现、营运资金与资本开支", prompt: "分析【公司】近三年的经营现金流、净利润、应收账款、存货和资本开支变化，说明利润兑现情况与现金流风险，注明数据期间和来源。" },
  { category: "industry", key: "industry-outlook", label: "查看行业观点", description: "查询最新行业或赛道周度观点", prompt: "查询【行业】最新周度观点，区分行业观点与赛道观点，并注明日期。" },
  { category: "industry", key: "industry-research", label: "研究行业赛道", description: "梳理产业链、供需和竞争格局", prompt: "研究【行业】的产业链、供需变化、竞争格局和增长驱动，列出关键观察指标与风险。" },
  { category: "industry", key: "industry-reports", label: "检索策略研报", description: "查找指定方向的近期研究观点", prompt: "检索【行业/策略方向】近期的 CICC／HTSC 研究报告，列出标题、日期和核心观点。" },
  { category: "industry", key: "industry-timing", label: "分析量化择时", description: "查看目标股票的技术面信号", prompt: "分析【股票代码/名称】当前量化择时信号，并说明主要技术指标和信号时点。" },
  { category: "industry", key: "industry-sentiment", label: "查看市场情绪", description: "了解 A 股情绪与行业景气度", prompt: "查询 A 股市场情绪和【行业】景气度的近期变化，并说明数据日期。" },
  { category: "industry", key: "industry-index", label: "查看市场策略观点", description: "检索近期市场策略研究", prompt: "检索近期 A 股市场策略研究观点，列出报告日期和主要判断。" },
  { category: "industry", key: "industry-policy", label: "梳理行业政策影响", description: "识别政策变化、受益环节与风险", prompt: "梳理【行业】近期重要政策，注明发布日期和来源，区分已实施政策与征求意见稿，分析对供需、竞争格局及产业链各环节的影响。" },
  { category: "industry", key: "industry-institutions", label: "观察机构行为", description: "跟踪资金动向与行业配置变化", prompt: "结合可用机构行为和资金数据，分析【行业】近期机构配置与资金动向，注明数据日期、统计口径和来源，区分事实与推断；缺失数据请明确说明。" },
  { category: "experts", key: "expert-global", label: "全球宏观与资产配置", description: "分析海外市场风格和跨资产传导", prompt: "从全球宏观和跨资产配置角度分析近期海外市场风格变化及其对本地资产的影响。" },
  { category: "experts", key: "expert-strategy", label: "A 股策略与行业配置", description: "研究行业配置、制度变化与主题机会", prompt: "分析 A 股行业配置、制度变化和主题投资机会，列出关键观察指标。" },
  { category: "experts", key: "expert-rates", label: "宏观利率与贵金属", description: "理解利率、汇率与资产配置影响", prompt: "分析美国利率变化对黄金、汇率和中国资产配置的影响。" },
  { category: "experts", key: "expert-macro", label: "全球宏观与国际金融", description: "关注通胀、国际金融秩序和利率", prompt: "分析全球通胀和国际金融秩序变化对利率与资产价格的影响。" },
  { category: "experts", key: "expert-energy", label: "新能源与电力设备", description: "研究新能源、储能和电网投资", prompt: "研究电力设备、新能源、储能和电网投资变化对产业链的影响。" },
  { category: "experts", key: "expert-fixed-income", label: "固收与资金面", description: "分析央行操作、流动性和机构行为", prompt: "从固收视角分析央行操作、资金面和机构行为对债券市场的影响。" },
  { category: "experts", key: "expert-consumption", label: "消费与出海", description: "观察食品饮料竞争和消费出海趋势", prompt: "分析食品饮料行业竞争变化、消费趋势和出海机会。" },
  { category: "experts", key: "expert-software", label: "软件与人工智能", description: "梳理赛道逻辑、代表方向和行情", prompt: "分析软件与人工智能行业的赛道逻辑、代表方向和近期板块变化。" },
  { category: "experts", key: "expert-retail", label: "零售、美妆与家居", description: "分析消费行业景气和渠道变化", prompt: "分析消费、零售、美妆或家居行业景气变化与渠道趋势。" },
  { category: "reports", key: "find-data", label: "查找研究数据", description: "整理所需数据及来源", prompt: "为【行业】研究列出所需数据，查找可用数据并注明来源和时间范围。" },
  { category: "reports", key: "find-articles", label: "搜索研究文章", description: "查找主题文章并归纳主要观点", prompt: "搜索【主题】相关研究文章，整理标题、发布日期和主要观点。" },
  { category: "reports", key: "find-reports", label: "检索研究报告", description: "按行业、方向或时间筛选报告", prompt: "按【行业】和【时间范围】检索 CICC／HTSC 研究报告，列出标题、日期和摘要。" },
  { category: "reports", key: "report-ratings", label: "筛选评级变化", description: "关注上调评级或首次覆盖报告", prompt: "筛选【时间范围】内【行业】评级上调或首次覆盖的研究报告。" },
  { category: "reports", key: "review-report", label: "核查报告内容", description: "检查数据、论据和结论是否一致", prompt: "核对这份报告中的关键数字、引用、论据和结论，标出需要进一步确认的内容。" },
  { category: "reports", key: "write-report", label: "整理研究报告", description: "按研究报告结构组织材料", prompt: "按研究报告结构整理这些材料，明确核心观点、论据、风险和待补充数据。" },
  { category: "reports", key: "cicc-report-search", label: "查询 CICC 研究观点", description: "按主题检索相关研报", prompt: "检索 CICC 对【主题】的研究报告，列出标题、日期和核心观点。" },
  { category: "reports", key: "htsc-report-search", label: "查询 HTSC 研究观点", description: "按主题检索华泰相关研报", prompt: "检索 HTSC 对【主题】的研究报告，列出标题、日期和摘要。" },
  { category: "review", key: "earnings-review", label: "点评公司业绩", description: "提炼财务指标、盈利驱动与风险", prompt: "点评【公司】最新一期业绩，提炼关键财务指标、盈利驱动、经营亮点和潜在风险。" },
  { category: "review", key: "daily-review", label: "复盘最近一个交易日", description: "总结 A 股主要指数和板块表现", prompt: "查询最近一个交易日的 A 股复盘，梳理主要指数、行业板块表现和市场驱动因素。" },
  { category: "review", key: "weekly-review", label: "复盘最近五个交易日", description: "观察一周市场走势与主要变化", prompt: "查询最近五个交易日的 A 股复盘，总结市场走势、主要板块表现和后续观察要点。" },
  { category: "review", key: "review-sector", label: "总结市场行业表现", description: "结合研究观点梳理板块驱动", prompt: "结合近期 CICC／HTSC 研究观点，复盘 A 股行业表现和市场驱动因素。" },
  { category: "review", key: "review-strategy", label: "查看策略复盘观点", description: "检索市场复盘和策略研究", prompt: "检索近期市场复盘或策略研究观点，归纳主要变化与后续关注点。" },
  { category: "review", key: "review-company-reports", label: "查业绩研究观点", description: "结合研究报告理解业绩变化", prompt: "检索【公司】近期业绩点评或市场策略报告，归纳主要观点。" },
  { category: "review", key: "review-company-drivers", label: "分析业绩变化原因", description: "梳理经营数据与盈利驱动", prompt: "结合【公司】的经营和财务数据，分析业绩变化的主要原因及后续关注点。" },
  { category: "review", key: "review-expectations", label: "对比业绩与预期", description: "核对实际业绩、历史指引与研究预测", prompt: "对比【公司】最新一期实际业绩与业绩公布前的公司指引或研究预测，统一期间和指标口径，梳理超预期或低于预期的项目及原因；注明来源和预测发布日期，没有可比预期时明确说明。" },
  { category: "bond", key: "bond-daily", label: "生成债市日报", description: "回顾最近交易日债券市场", prompt: "生成最近一个交易日的债券市场日报，概括资金面、债券行情和后续关注点。" },
  { category: "bond", key: "bond-credit", label: "分析主体信用", description: "评估偿债能力、债务结构和信用风险", prompt: "分析【发行人全称/债券代码】的主体资质、偿债能力、债务结构和主要信用风险。" },
  { category: "bond", key: "bond-curve", label: "查询国债收益率曲线", description: "查看关键期限收益率", prompt: "拉中债国债曲线，看 1Y、10Y 关键期限收益率和期限利差。" },
  { category: "bond", key: "bond-info", label: "查询债券基础信息", description: "查看票面利率、期限与剩余期限", prompt: "查询【债券名称/代码】基础信息，包括票面利率、发行期限和剩余期限。" },
  { category: "bond", key: "bond-primary", label: "跟踪一级市场发行", description: "查看本周债券发行情况", prompt: "查询本周信用债一级发行情况，整理发行规模、票面利率和认购情况。" },
  { category: "bond", key: "bond-offshore", label: "查询境外债收益率", description: "查看境外债近期收益率走势", prompt: "查询目标境外债最近可用的收益率走势和近期变化。" },
  { category: "bond", key: "bond-maturity", label: "梳理债务到期压力", description: "观察到期分布和再融资压力", prompt: "梳理【发行人】未来一年的债券到期分布、到期规模和再融资压力。" },
  { category: "bond", key: "bond-risk-watch", label: "建立信用风险观察清单", description: "整理需持续跟踪的偿债指标", prompt: "列出【发行人】需要持续跟踪的偿债能力和信用风险指标。" },
  { category: "multi-asset", key: "money-market", label: "查询资金利率", description: "查看 DR007、R007 等指标", prompt: "查询今天 DR007、R007 的数值和对应日期。" },
  { category: "multi-asset", key: "bill-market", label: "查看票据报价", description: "查询票据转贴市场报价", prompt: "查询今天票据转贴市场报价，并汇总买入和卖出报价数量。" },
  { category: "multi-asset", key: "macro-indicator", label: "查询宏观指标", description: "查看指标定义、频率和最新日期", prompt: "查询制造业 PMI 指标信息，包括指标名称、频率和最新数据日期。" },
  { category: "multi-asset", key: "futures-market", label: "查询期货行情", description: "查看指定国债期货合约", prompt: "查询指定国债期货合约行情，包括收盘价、持仓量和成交量。" },
  { category: "multi-asset", key: "fx-market", label: "查询汇率行情", description: "查看美元兑人民币日行情", prompt: "查询美元兑人民币日行情，并概括指定区间的变化。" },
  { category: "multi-asset", key: "fund-profile", label: "查询基金信息", description: "查看基金规模、经理和成立日期", prompt: "查询【基金名称/代码】基本信息，包括基金规模、基金经理和成立日期。" },
  { category: "multi-asset", key: "company-profile-dm", label: "查询企业工商信息", description: "查看注册信息和实际控制人", prompt: "查询【企业名称】工商信息，包括成立日期、注册资本和实际控制人。" },
  { category: "multi-asset", key: "cross-asset-performance", label: "分析大类资产表现", description: "比较股债商品汇率与宏观驱动", prompt: "比较【时间范围】内股票、债券、商品和汇率等主要资产的表现，梳理增长、通胀、利率和流动性变化的影响。明确各类资产的代表指数或指标、统计口径、数据日期和来源，区分事实与推断；缺失数据请明确说明。" },
];

function WelcomePrompts({ onSelect }: { onSelect: (prompt: string) => void }) {
  const [category, setCategory] = useState("company");
  const [showAll, setShowAll] = useState(false);
  const prompts = WELCOME_PROMPTS.filter((item) => item.category === category);
  const visiblePrompts = showAll ? prompts : prompts.slice(0, 8);
  return (
    <div className="welcome-services">
      <div className="welcome-category-list" role="group" aria-label="研究分类">
        {WELCOME_CATEGORIES.map((item) => (
          <button key={item.key} type="button" className={`welcome-category${category === item.key ? " is-active" : ""}`}
            aria-pressed={category === item.key} onClick={() => { setCategory(item.key); setShowAll(false); }}>
            <Icon name={item.icon} size={16} />
            <span className="welcome-category-name">{item.label}</span>
          </button>
        ))}
      </div>
      <section className="welcome-prompts-section" aria-label="推荐问题" aria-live="polite">
        <div className="prompt-grid">
          {visiblePrompts.map(({ key, label, description, prompt }) => (
            <button type="button" key={key} className="welcome-prompt" onClick={() => onSelect(prompt)}>
              <span className="welcome-prompt-title">{label}</span>
              <span className="welcome-prompt-description">{description}</span>
            </button>
          ))}
        </div>
        {prompts.length > 8 && <button type="button" className="welcome-more-button" aria-expanded={showAll}
          onClick={() => setShowAll((value) => !value)}>{showAll ? "收起问题" : "更多问题"}<span aria-hidden="true">{showAll ? "↑" : "→"}</span></button>}
      </section>
    </div>
  );
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
  const assistantName = useAssistantName();
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

  const chat = useChatStream({ scroll, active });
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
    <ArtifactWorkspace key={`${session.userId}:${session.conversationId ?? "new"}:${conversationProjectId ?? ""}`} userId={session.userId} conversationId={session.conversationId} projectId={conversationProjectId ?? null} projectName={projectName} messages={chatMatchesSelection ? chat.state.messages : []} onLocateMessage={chat.loadMessage}>
    <div className="chat-view">
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
        ) : !hasMessages && !chat.state.approval && !chat.state.error ? (
          <div className="welcome">
            <div className="welcome-mark">
              <img
                src={__MELONCLAW_IS_RMS_BRAND__ ? "/assets/brand/melonclaw-word-rms.png" : "/assets/brand/melonclaw-word-transparent.webp"}
                alt={__MELONCLAW_IS_RMS_BRAND__ ? "RMS" : "MelonClaw"}
              />
            </div>
            <h1>今天想研究什么？</h1>
            <WelcomePrompts onSelect={setDraft} />
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
