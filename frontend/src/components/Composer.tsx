import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
} from "react";
import { App as AntdApp } from "antd";
import Sender, { type SenderRef } from "@ant-design/x/es/sender";

import { AttachmentDialog, type StagedAttachment } from "./AttachmentDialog";
import { Icon } from "./Icon";
import { ImageLightbox } from "./ImageLightbox";
import { ModelPicker } from "./ModelPicker";
import { PlusMenu } from "./PlusMenu";
import { ProjectPicker } from "./ProjectPicker";
import { SkillPicker } from "./SkillPicker";
import { SkillLogo } from "./SkillLogo";
import { findSkillTrigger, type SkillTrigger } from "../lib/skillTrigger";
import { attachmentBadge, formatBytes } from "../lib/attachmentFiles";
import { useAttachmentCapabilities } from "../hooks/useAttachmentCapabilities";
import { useSession } from "../state/session";
import {
  attachmentContentUrl,
  deleteAttachment,
  getAttachment,
  retryAttachmentParse,
} from "../api/client";
import type { AttachmentSummary, SkillOption } from "../types/api";

const EMPTY_SKILLS: SkillOption[] = [];
/** 解析轮询上限，略大于后端 attachment_parse_timeout_seconds（默认 300s）。 */
const PARSE_POLL_TIMEOUT_MS = 330_000;
const PARSE_POLL_INITIAL_MS = 1000;
const PARSE_POLL_MAX_MS = 5000;

export interface ComposerProps {
  /** 从 Skill 管理页的“尝试”入口带入新会话的初始 Skill。 */
  initialSkill?: SkillOption | null;
  onInitialSkillApplied?: () => void;
  value: string;
  onChange: (value: string) => void;
  onSend: (
    value: string,
    skillId?: string | null,
    attachmentIds?: string[],
    onAccepted?: () => void,
  ) => void;
  disabled: boolean;
  pendingInteraction: "question" | "approval" | null;
  /** 当前会话是否有 AI 输出在跑：跑时发送键变方形停止键。 */
  isRunning?: boolean;
  /** 显式取消当前会话输出；只在停止模式下调用。 */
  onStop?: () => void;
  onOpenProjectDialog?: () => void;
  /** 打开技能|连接器页的模型供应商 TAB（模型选择器底部「添加自定义模型」）。 */
  onOpenModelSettings?: () => void;
}

/** 已通过弹窗确认、进入输入区等待随消息发送的附件。 */
type ComposerAttachment = AttachmentSummary & {
  source: "upload";
  file?: File;
  clientRequestId?: string;
  /** 轮询超过上限仍未完成解析。 */
  parseTimedOut?: boolean;
  statusCheckFailed?: boolean;
};

function isReady(attachment: ComposerAttachment): boolean {
  if (attachment.parseTimedOut) return false;
  return (attachment.kind === "image" || attachment.kind === "archive")
    ? attachment.parse_status === "not_required"
    : attachment.parse_status === "processed";
}

function isPolling(attachment: ComposerAttachment): boolean {
  return (
    attachment.source === "upload" &&
    !attachment.parseTimedOut &&
    (attachment.parse_status === "pending" || attachment.parse_status === "processing")
  );
}

function statusText(attachment: ComposerAttachment): string {
  if (attachment.parseTimedOut) return "解析超时";
  if (attachment.statusCheckFailed) return "连接中断，正在重新查询解析状态…";
  if (attachment.parse_status === "failed") return "解析失败";
  if (attachment.parse_status === "processed" || attachment.parse_status === "not_required") {
    return formatBytes(attachment.size_bytes);
  }
  return "解析中…";
}

/** Sender 仅负责输入展示；模型快照与发送恢复仍由现有聊天流管理。 */
export function Composer({
  initialSkill = null,
  onInitialSkillApplied,
  value,
  onChange,
  onSend,
  disabled,
  pendingInteraction,
  isRunning,
  onStop,
  onOpenProjectDialog,
  onOpenModelSettings,
}: ComposerProps) {
  const session = useSession();
  const attachmentAccess = useMemo(
    () =>
      session.projectId
        ? { userId: session.userId, projectId: session.projectId }
        : { userId: session.userId, conversationId: session.conversationId },
    [session.conversationId, session.projectId, session.userId],
  );
  const { message } = AntdApp.useApp();
  const senderRef = useRef<SenderRef>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const skillPrefixRef = useRef<HTMLDivElement>(null);
  const composingRef = useRef(false);
  const submittingRef = useRef(false);
  const [skillTrigger, setSkillTrigger] = useState<SkillTrigger | null>(null);
  const [activeSkillIndex, setActiveSkillIndex] = useState(0);
  const [selectedSkill, setSelectedSkill] = useState<SkillOption | null>(null);
  const [skillPrefixWidth, setSkillPrefixWidth] = useState(0);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<ComposerAttachment | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogFiles, setDialogFiles] = useState<File[]>([]);
  const [dialogSessionId, setDialogSessionId] = useState(0);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  const dragDepthRef = useRef(0);
  const notifiedParseFailureRef = useRef(new Set<string>());
  const capabilities = useAttachmentCapabilities();

  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);

  const skills = session.skills ?? EMPTY_SKILLS;
  const filteredSkills = useMemo(() => {
    const query = skillTrigger?.query.trim().toLocaleLowerCase() ?? "";
    if (!query) return skills;
    return skills.filter((skill) =>
      `${skill.id} ${skill.display_name} ${skill.description}`
        .toLocaleLowerCase()
        .includes(query),
    );
  }, [skillTrigger?.query, skills]);

  const updateSkillTrigger = (
    nextValue: string,
    input?: HTMLTextAreaElement | null,
  ) => {
    const target = input ?? (senderRef.current?.inputElement as HTMLTextAreaElement | undefined);
    const cursor = target?.selectionStart ?? nextValue.length;
    const selectionEnd = target?.selectionEnd ?? cursor;
    const nextTrigger = findSkillTrigger(nextValue, cursor, selectionEnd);
    setSkillTrigger(nextTrigger);
    if (!nextTrigger) {
      setActiveSkillIndex(0);
      return;
    }
    setActiveSkillIndex((index) => {
      const nextLength = skills.filter((skill) => {
        const query = nextTrigger.query.trim().toLocaleLowerCase();
        return !query || `${skill.id} ${skill.display_name} ${skill.description}`
          .toLocaleLowerCase()
          .includes(query);
      }).length;
      return nextLength > 0 ? Math.min(index, nextLength - 1) : 0;
    });
  };

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!composerRef.current?.contains(event.target as Node)) {
        setSkillTrigger(null);
        setPlusMenuOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPlusMenuOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  useEffect(() => {
    // 技能选择只对当前上下文的一条消息生效，切换用户/项目/会话时不能带到下一处。
    if (session.conversationId && session.conversationId === session.draftConversationId) return;
    setSelectedSkill(null);
    setSkillTrigger(null);
    // draftConversationId 清除于首条消息落库，不代表切换聊天。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.userId, session.projectId, session.conversationId]);

  useEffect(() => {
    if (!initialSkill) return;
    setSelectedSkill(initialSkill);
    setSkillTrigger(null);
    onInitialSkillApplied?.();
  }, [initialSkill, onInitialSkillApplied]);

  useEffect(() => {
    // 空白页因附件上传取得 ID 后仍是同一张草稿，弹窗和暂存附件应保留。
    if (!(session.conversationId && session.conversationId === session.draftConversationId)) {
      setAttachments([]);
      setPreview(null);
      setDragging(false);
      setDialogOpen(false);
      setPlusMenuOpen(false);
      dragDepthRef.current = 0;
    }
    return () => {
      for (const attachment of attachmentsRef.current) {
        if (attachment.source === "upload" && attachment.status === "staged") {
          void deleteAttachment(attachment.attachment_id, attachmentAccess).catch(() => undefined);
        }
      }
    };
    // 首条消息落库后保留已附加文件，因此不随 draftConversationId 清除重跑。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachmentAccess, session.userId, session.projectId, session.conversationId]);

  // 解析轮询：按附件 ID 集合调度，指数退避并在超过上限后标记超时。
  // 依赖只有稳定的 pendingKey，不会因为每次轮询回写状态而重建定时器。
  const pendingKey = useMemo(
    () =>
      attachments
        .filter(isPolling)
        .map((attachment) => attachment.attachment_id)
        .join(","),
    [attachments],
  );

  useEffect(() => {
    if (!pendingKey) return undefined;
    const ids = pendingKey.split(",");
    let cancelled = false;
    let timer: number | undefined;
    let delay = PARSE_POLL_INITIAL_MS;
    const startedAt = Date.now();

    const tick = async () => {
      const results = await Promise.all(
        ids.map((id) =>
          getAttachment(id, attachmentAccess)
            .catch(() => null),
        ),
      );
      if (cancelled) return;
      const stillPending = results.some((result) => !result || result.parse_status === "pending" || result.parse_status === "processing");
      setAttachments((current) =>
        current.map((item) => {
          const next = results.find((result) => result?.attachment_id === item.attachment_id);
          if (!next) {
            if (ids.includes(item.attachment_id) && isPolling(item)) {
              return { ...item, statusCheckFailed: true };
            }
            return item;
          }
          if (next.parse_status === "failed" && !notifiedParseFailureRef.current.has(item.attachment_id)) {
            notifiedParseFailureRef.current.add(item.attachment_id);
            message.error(`${next.file_name} 解析失败，可以重试。`);
          }
          return { ...item, ...next, parseTimedOut: false, statusCheckFailed: false };
        }),
      );
      if (!stillPending) return;
      if (Date.now() - startedAt >= PARSE_POLL_TIMEOUT_MS) {
        setAttachments((current) =>
          current.map((item) =>
            ids.includes(item.attachment_id) ? { ...item, parseTimedOut: true } : item,
          ),
        );
        return;
      }
      delay = Math.min(Math.round(delay * 1.5), PARSE_POLL_MAX_MS);
      timer = window.setTimeout(() => void tick(), delay);
    };

    timer = window.setTimeout(() => void tick(), delay);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [attachmentAccess, pendingKey, message, session.userId]);

  useLayoutEffect(() => {
    if (!selectedSkill) {
      setSkillPrefixWidth(0);
      return;
    }
    setSkillPrefixWidth(skillPrefixRef.current?.offsetWidth ?? 0);
  }, [selectedSkill]);

  useEffect(() => {
    setActiveSkillIndex((index) =>
      filteredSkills.length > 0 ? Math.min(index, filteredSkills.length - 1) : 0,
    );
  }, [filteredSkills.length]);

  let placeholder = "聊点儿什么，输入/调用技能工具。";
  // 停止模式：当前会话正在输出且非等待确认，发送键变方形停止键，可点取消。
  // waiting（审批/问题卡）时仍是等待确认，不进停止模式。
  const running = isRunning ?? session.busy;
  const waiting = pendingInteraction !== null;
  const stopMode = running && !waiting && !session.conversationCreating;
  if (!session.contextReady) {
    placeholder = "正在准备工作区…";
  } else if (session.conversationCreating) {
    placeholder = "正在准备会话…";
  } else if (waiting) {
    placeholder = pendingInteraction === "question"
      ? "请先回答上方问题，也可以先写下一条消息…"
      : "请先确认上方操作，也可以先写下一条消息…";
  } else if (running) {
    placeholder = "助手正在回复，可以先写下一条消息…";
  }

  const canUse =
    session.contextReady &&
    session.status?.status === "ready";

  const inputDisabled = !canUse;
  const canAttach = canUse && !session.conversationCreating;
  const modelOptions = session.modelOptions ?? [];
  const selectedModelId =
    modelOptions.find((item) => item.id === session.selectedModelId && item.available)?.id
    || modelOptions.find((item) => item.available)?.id || "";
  const selectedModel = modelOptions.find((item) => item.id === selectedModelId);
  const hasUnsupportedImage = attachments.some(
    (attachment) => attachment.kind === "image" &&
      !(selectedModel?.input_modalities ?? []).includes("image"),
  );
  const readyAttachments = attachments.length > 0 && attachments.every(isReady) && !hasUnsupportedImage;
  const sendDisabled = stopMode
    ? false
    : (!selectedModel?.available || inputDisabled || disabled || running || waiting || session.conversationCreating || (attachments.length > 0 && !readyAttachments) || (!value.trim() && !readyAttachments));
  const sendLabel = stopMode
    ? "停止生成"
    : session.conversationCreating
      ? "准备中"
      : waiting
        ? pendingInteraction === "question" ? "等待回答" : "等待确认"
        : running
          ? "处理中"
          : "发送";
  const sendIcon = stopMode
    ? "square"
    : session.conversationCreating || running
      ? "loader-circle"
      : waiting
        ? "shield-check"
        : "arrow-up";

  /** 打开附件弹窗；带 files 表示由输入区的拖拽/粘贴进入，弹窗会立刻上传。 */
  const openAttachmentDialog = async (files: File[] = []) => {
    if (!canAttach) return;
    let conversationId = session.conversationId;
    if (!conversationId && !session.projectId) {
      const conversation = await session.ensureConversation();
      conversationId = conversation?.id ?? null;
    }
    if (!conversationId && !session.projectId) return;
    setPlusMenuOpen(false);
    setDialogFiles(files);
    setDialogSessionId((current) => current + 1);
    setDialogOpen(true);
  };

  const submit = () => {
    // 停止模式下回车不发送：只能点按钮显式取消，避免误触。
    if (stopMode) return;
    if (sendDisabled || composingRef.current || submittingRef.current) return;
    submittingRef.current = true;
    try {
      const ids = attachments.map((attachment) => attachment.attachment_id);
      if (ids.length === 0) {
        if (selectedSkill) onSend(value, selectedSkill.id);
        else onSend(value);
      } else if (selectedSkill) {
        onSend(value, selectedSkill.id, ids, () => setAttachments([]));
      } else {
        onSend(value, null, ids, () => setAttachments([]));
      }
    } finally {
      setSelectedSkill(null);
      queueMicrotask(() => { submittingRef.current = false; });
    }
  };

  /** 发送键点击：停止模式下显式取消，否则走正常发送。 */
  const handleSendButton = () => {
    if (stopMode) {
      onStop?.();
      return;
    }
    submit();
  };

  const handleDialogConfirm = (items: StagedAttachment[]) => {
    if (items.length === 0) return;
    setAttachments((current) => [
      ...current,
      ...items.map((item) => ({ ...item, source: "upload" as const })),
    ]);
  };

  const removeAttachment = (attachment: ComposerAttachment) => {
    if (preview?.attachment_id === attachment.attachment_id) setPreview(null);
    setAttachments((current) => current.filter((item) => item.attachment_id !== attachment.attachment_id));
    if (attachment.source === "upload" && attachment.status === "staged") {
      void deleteAttachment(attachment.attachment_id, attachmentAccess).catch(() => undefined);
    }
  };

  const retryParse = (attachment: ComposerAttachment) => {
    notifiedParseFailureRef.current.delete(attachment.attachment_id);
    setAttachments((current) =>
      current.map((item) =>
        item.attachment_id === attachment.attachment_id
          ? { ...item, parse_status: "pending", parse_error_code: null, parseTimedOut: false }
          : item,
      ),
    );
    void retryAttachmentParse(attachment.attachment_id, {
      userId: session.userId,
      projectId: session.projectId || null,
      conversationId: session.projectId ? null : session.conversationId,
    }).catch((error: unknown) => {
      const text = error instanceof Error ? error.message : "重新解析失败，请稍后重试。";
      message.error(text);
      setAttachments((current) =>
        current.map((item) =>
          item.attachment_id === attachment.attachment_id
            ? { ...item, parse_status: "failed" }
            : item,
        ),
      );
    });
  };

  const handleDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!canAttach || !event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragging(true);
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!canAttach || !event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!canAttach || !event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragging(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!canAttach) return;
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length > 0) void openAttachmentDialog(files);
  };

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (!canAttach) return;
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length === 0) return;
    event.preventDefault();
    void openAttachmentDialog(files);
  };

  const selectSkill = (skill: SkillOption) => {
    if (!skillTrigger) return;
    const nextValue = value.slice(0, skillTrigger.start) + value.slice(skillTrigger.end);
    const nextCursor = skillTrigger.start;
    onChange(nextValue);
    setSelectedSkill(skill);
    setSkillTrigger(null);
    setActiveSkillIndex(0);
    window.requestAnimationFrame(() => {
      const input = senderRef.current?.inputElement as HTMLTextAreaElement | undefined;
      input?.focus();
      input?.setSelectionRange(nextCursor, nextCursor);
    });
  };

  /** 从加号二级目录选择技能：没有 `/` 触发词需要清理，只挂上技能标签。 */
  const applySkill = (skill: SkillOption) => {
    setPlusMenuOpen(false);
    setSkillTrigger(null);
    setSelectedSkill(skill);
    window.requestAnimationFrame(() => {
      const input = senderRef.current?.inputElement as HTMLTextAreaElement | undefined;
      input?.focus();
    });
  };

  return (
    <div className="composer-wrap">
      <ProjectPicker onOpenProjectDialog={onOpenProjectDialog} />
      <div
        ref={composerRef}
        className={`composer ${dragging ? "is-dragging" : ""}`}
        onCompositionStartCapture={() => { composingRef.current = true; }}
        onCompositionEndCapture={() => { composingRef.current = false; }}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onPaste={handlePaste}
      >
        {dragging ? (
          <div className="composer-dropzone" aria-hidden="true">
            <Icon name="plus" size={18} />松开后添加附件
          </div>
        ) : null}
        {skillTrigger ? (
          <SkillPicker
            skills={filteredSkills}
            activeIndex={activeSkillIndex}
            loading={session.skillsLoading ?? false}
            error={session.skillsError ?? null}
            onSelect={selectSkill}
            onHover={setActiveSkillIndex}
          />
        ) : null}
        {plusMenuOpen ? (
          <PlusMenu
            skills={skills}
            skillsLoading={session.skillsLoading ?? false}
            skillsError={session.skillsError ?? null}
            onPickFiles={() => void openAttachmentDialog()}
            onPickSkill={applySkill}
          />
        ) : null}
        {attachments.length > 0 ? (
          <div className="composer-attachments" aria-label="当前消息附件">
            {attachments.map((attachment) => {
              const url = attachmentContentUrl(attachment.attachment_id, {
                userId: session.userId,
                projectId: session.projectId || null,
                conversationId: session.projectId ? null : session.conversationId,
              });
              const failed = attachment.parse_status === "failed" || attachment.parseTimedOut;
              return (
                <div className={`composer-attachment ${failed ? "has-error" : ""}`} key={attachment.attachment_id}>
                  {attachment.kind === "image" ? (
                    <button
                      type="button"
                      className="composer-attachment-thumb"
                      aria-label={`预览 ${attachment.file_name}`}
                      onClick={() => setPreview(attachment)}
                    >
                      <img className="composer-attachment-preview" src={url} alt="" />
                    </button>
                  ) : (
                    <span className="attachment-file-icon" aria-hidden="true">
                      <Icon name="file-text" size={15} />
                      <span className="attachment-badge">{attachmentBadge(attachment.file_name)}</span>
                    </span>
                  )}
                  <span className="composer-attachment-body">
                    <span className="composer-attachment-name" title={attachment.file_name}>{attachment.file_name}</span>
                    <span className="composer-attachment-status">{statusText(attachment)}</span>
                  </span>
                  {attachment.parse_status === "failed" || attachment.parseTimedOut ? (
                    <button type="button" onClick={() => retryParse(attachment)}>重新解析</button>
                  ) : null}
                  <button
                    type="button"
                    className="composer-attachment-remove"
                    aria-label={`移除 ${attachment.file_name}`}
                    onClick={() => removeAttachment(attachment)}
                  >
                    <Icon name="x" size={13} />
                  </button>
                </div>
              );
            })}
          </div>
        ) : null}
        <Sender
          ref={senderRef}
          className="melon-sender"
          value={value}
          placeholder={selectedSkill ? "" : placeholder}
          styles={{
            input: {
              textIndent: selectedSkill ? `${skillPrefixWidth + 6}px` : undefined,
            },
          }}
          aria-label="输入内容"
          aria-describedby="composer-hint"
          disabled={inputDisabled}
          loading={running || session.conversationCreating}
          onChange={(nextValue, event) => {
            onChange(nextValue);
            updateSkillTrigger(
              nextValue,
              event?.currentTarget as HTMLTextAreaElement | undefined,
            );
          }}
          onFocus={(event) => updateSkillTrigger(value, event.currentTarget)}
          onSubmit={submit}
          submitType="enter"
          autoSize={{ minRows: 1, maxRows: 6 }}
          prefix={
            selectedSkill ? (
              <div
                ref={skillPrefixRef}
                className="selected-skill"
                aria-label={`已选择技能 ${selectedSkill.display_name}`}
                aria-keyshortcuts="Backspace"
                title="按 Backspace 移除技能"
              >
                <SkillLogo />
                <span>{selectedSkill.display_name}</span>
              </div>
            ) : null
          }
          suffix={false}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229 || composingRef.current) return false;
            const input = event.currentTarget as HTMLTextAreaElement;
            if (
              selectedSkill &&
              !skillTrigger &&
              event.key === "Backspace" &&
              input.selectionStart === 0 &&
              input.selectionEnd === 0
            ) {
              event.preventDefault();
              setSelectedSkill(null);
              return false;
            }
            if (skillTrigger) {
              if (event.key === "Escape") {
                event.preventDefault();
                setSkillTrigger(null);
                return false;
              }
              if (event.key === "ArrowDown" && filteredSkills.length > 0) {
                event.preventDefault();
                setActiveSkillIndex((index) => (index + 1) % filteredSkills.length);
                return false;
              }
              if (event.key === "ArrowUp" && filteredSkills.length > 0) {
                event.preventDefault();
                setActiveSkillIndex((index) => (index - 1 + filteredSkills.length) % filteredSkills.length);
                return false;
              }
              if (
                (event.key === "Enter" || event.key === "Tab") &&
                !event.shiftKey &&
                !event.ctrlKey &&
                !event.altKey &&
                !event.metaKey &&
                filteredSkills.length > 0
              ) {
                event.preventDefault();
                selectSkill(filteredSkills[activeSkillIndex]);
                return false;
              }
            }
            if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
              event.preventDefault();
              submit();
              return false;
            }
          }}
          footer={
            <div className="composer-bottom">
              <div className="composer-bottom-left">
                <button
                  type="button"
                  className="attachment-button"
                  aria-label="添加附件"
                  title="添加内容：图片和文件，或调用技能"
                  disabled={!canAttach}
                  aria-haspopup="menu"
                  aria-expanded={plusMenuOpen}
                  onClick={() => setPlusMenuOpen((current) => !current)}
                >
                  <Icon name="plus" size={18} />
                </button>
              </div>
              <div className="composer-bottom-right">
                <ModelPicker
                    options={modelOptions}
                    value={selectedModelId}
                    onChange={(modelId) => session.selectModel?.(modelId)}
                    onAddCustomModel={onOpenModelSettings}
                />
                <button
                  className="send-button"
                  type="button"
                  onClick={handleSendButton}
                  disabled={sendDisabled}
                  aria-label={sendLabel}
                  aria-busy={running || session.conversationCreating}
                  title={sendLabel}
                >
                  <Icon
                    name={sendIcon}
                    size={18}
                    className={sendIcon === "loader-circle" ? "send-arrow mc-icon-spin" : "send-arrow"}
                  />
                </button>
              </div>
            </div>
          }
        />
      </div>
      {hasUnsupportedImage ? <div className="composer-attachment-warning" role="alert">当前模型不支持图片附件，请切换支持图片的模型或移除图片后再发送。</div> : null}
      {attachments.some((item) => item.parse_status === "failed" || item.parseTimedOut) ? <div className="composer-attachment-warning" role="alert">附件解析失败或超时，请点击附件旁的重试按钮，或移除附件后再发送。</div> : attachments.some(isPolling) ? <div className="composer-attachment-warning" role="status">附件尚未完成解析，完成后才能发送。文字草稿会保留。</div> : null}
      <div className="composer-meta">
        <div className="composer-hint" id="composer-hint">
          <Icon name="message-circle" size={15} /> Enter 发送 · Shift + Enter 换行 · 支持拖拽或粘贴附件
        </div>
        <div className="composer-footnote">AI生成内容仅供参考。</div>
      </div>
      {preview ? (
        <ImageLightbox
          src={attachmentContentUrl(preview.attachment_id, {
            userId: session.userId,
            projectId: session.projectId || null,
            conversationId: session.projectId ? null : session.conversationId,
          })}
          alt={preview.file_name}
          onClose={() => setPreview(null)}
        />
      ) : null}
      <AttachmentDialog
        open={dialogOpen}
        sessionId={dialogSessionId}
        initialFiles={dialogFiles}
        capabilities={capabilities}
        userId={session.userId}
        projectId={session.projectId || null}
        conversationId={session.conversationId ?? ""}
        existingCount={attachments.length}
        existingTotalBytes={attachments.reduce((sum, item) => sum + item.size_bytes, 0)}
        onClose={() => setDialogOpen(false)}
        onConfirm={handleDialogConfirm}
      />
    </div>
  );
}
