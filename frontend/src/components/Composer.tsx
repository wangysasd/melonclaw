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
import { Select } from "antd";
import Sender, { type SenderRef } from "@ant-design/x/es/sender";

import { Icon } from "./Icon";
import { ImageLightbox } from "./ImageLightbox";
import { SkillPicker } from "./SkillPicker";
import { SkillLogo } from "./SkillLogo";
import { findSkillTrigger, type SkillTrigger } from "../lib/skillTrigger";
import {
  attachmentBadge,
  attachmentKindOf,
  extensionOf,
  formatBytes,
  validateAttachmentFile,
} from "../lib/attachmentFiles";
import { useAttachmentCapabilities } from "../hooks/useAttachmentCapabilities";
import { useSession } from "../state/session";
import {
  attachmentContentUrl,
  deleteAttachment,
  getAttachment,
  retryAttachmentParse,
  uploadAttachment,
} from "../api/client";
import type { AttachmentSummary, SkillOption } from "../types/api";

const EMPTY_SKILLS: SkillOption[] = [];
/** 解析轮询上限，略大于后端 attachment_parse_timeout_seconds（默认 300s）。 */
const PARSE_POLL_TIMEOUT_MS = 330_000;
const PARSE_POLL_INITIAL_MS = 1000;
const PARSE_POLL_MAX_MS = 5000;

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (
    value: string,
    skillId?: string | null,
    attachmentIds?: string[],
    onAccepted?: () => void,
  ) => void;
  disabled: boolean;
}

type ComposerAttachment = AttachmentSummary & {
  source: "upload";
  file?: File;
  clientRequestId?: string;
  uploading?: boolean;
  uploadError?: string | null;
  /** 上传进度百分比（0-100）。 */
  progress?: number;
  /** 轮询超过上限仍未完成解析。 */
  parseTimedOut?: boolean;
};

function isReady(attachment: ComposerAttachment): boolean {
  if (attachment.uploading || attachment.parseTimedOut) return false;
  return attachment.kind === "image"
    ? attachment.parse_status === "not_required"
    : attachment.parse_status === "processed";
}

function isPolling(attachment: ComposerAttachment): boolean {
  return (
    attachment.source === "upload" &&
    !attachment.uploading &&
    !attachment.uploadError &&
    !attachment.parseTimedOut &&
    (attachment.parse_status === "pending" || attachment.parse_status === "processing")
  );
}

function statusText(attachment: ComposerAttachment): string {
  if (attachment.uploading) {
    return attachment.progress ? `上传中 ${attachment.progress}%` : "上传中…";
  }
  if (attachment.uploadError) return "上传失败";
  if (attachment.parseTimedOut) return "解析超时";
  if (attachment.parse_status === "failed") return "解析失败";
  if (attachment.parse_status === "processed" || attachment.parse_status === "not_required") {
    return formatBytes(attachment.size_bytes);
  }
  return "解析中…";
}

/** Sender 仅负责输入展示；模型快照与发送恢复仍由现有聊天流管理。 */
export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  const session = useSession();
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
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<ComposerAttachment | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const attachmentsRef = useRef<ComposerAttachment[]>([]);
  const uploadControllersRef = useRef(new Map<string, AbortController>());
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
      }
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, []);

  useEffect(() => {
    // 技能选择只对当前上下文的一条消息生效，切换用户/项目/会话时不能带到下一处。
    setSelectedSkill(null);
    setSkillTrigger(null);
  }, [session.userId, session.projectId, session.conversationId]);

  useEffect(() => {
    setAttachments([]);
    setFileError(null);
    setPreview(null);
    setDragging(false);
    dragDepthRef.current = 0;
    const uploadControllers = uploadControllersRef.current;
    return () => {
      for (const controller of uploadControllers.values()) controller.abort();
      uploadControllers.clear();
      for (const attachment of attachmentsRef.current) {
        if (attachment.source === "upload" && attachment.status === "staged" && !attachment.attachment_id.startsWith("uploading-")) {
          void deleteAttachment(attachment.attachment_id, { userId: session.userId, tenantId: session.tenantId }).catch(() => undefined);
        }
      }
    };
  }, [session.userId, session.tenantId, session.projectId, session.conversationId]);

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
          getAttachment(id, { userId: session.userId, tenantId: session.tenantId })
            .catch(() => null),
        ),
      );
      if (cancelled) return;
      let stillPending = false;
      setAttachments((current) =>
        current.map((item) => {
          const next = results.find((result) => result?.attachment_id === item.attachment_id);
          if (!next) return item;
          if (isPolling({ ...item, ...next })) {
            stillPending = true;
          } else if (next.parse_status === "failed" && !notifiedParseFailureRef.current.has(item.attachment_id)) {
            notifiedParseFailureRef.current.add(item.attachment_id);
            message.error(`${next.file_name} 解析失败，可以重试。`);
          }
          return { ...item, ...next, parseTimedOut: false };
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
  }, [pendingKey, message, session.userId, session.tenantId]);

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
  if (!session.contextReady) {
    placeholder = "正在准备工作区…";
  } else if (session.projects.length === 0) {
    placeholder = "请先创建一个项目…";
  } else if (session.conversationCreating) {
    placeholder = "正在准备会话…";
  } else if (session.runStatus === "waiting") {
    placeholder = "请先处理待确认操作，也可以先写下一条消息…";
  } else if (session.busy) {
    placeholder = "助手正在回复，可以先写下一条消息…";
  }

  const canUse =
    session.contextReady &&
    session.status?.status === "ready" &&
    session.projects.length > 0;

  const inputDisabled = !canUse;
  const canAttach = canUse && !session.conversationCreating;
  const modelOptions = session.modelOptions ?? [];
  const selectedModelId =
    session.selectedModelId || modelOptions.find((item) => item.available)?.id || "";
  const selectedModel = modelOptions.find((item) => item.id === selectedModelId);
  const hasUnsupportedImage = attachments.some(
    (attachment) => attachment.kind === "image" &&
      selectedModel?.input_modalities && !selectedModel.input_modalities.includes("image"),
  );
  const readyAttachments = attachments.length > 0 && attachments.every(isReady) && !hasUnsupportedImage;
  const sendDisabled = inputDisabled || disabled || session.busy || session.runStatus === "waiting" || session.conversationCreating || (!value.trim() && !readyAttachments);
  const sendLabel = session.conversationCreating
    ? "准备中"
    : session.runStatus === "waiting"
      ? "等待确认"
      : session.busy
        ? "处理中"
        : "发送";
  const sendIcon = session.conversationCreating || session.busy
    ? "loader-circle"
    : session.runStatus === "waiting"
      ? "shield-check"
      : "arrow-up";
  const acceptExtensions = capabilities.items.map((item) => item.extension).join(",");

  const submit = () => {
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

  const startUpload = (file: File) => {
    if (!session.projectId || !session.userId) return;
    const extension = extensionOf(file.name);
    const clientRequestId = crypto.randomUUID();
    const temporaryId = `uploading-${clientRequestId}`;
    const placeholder: ComposerAttachment = {
      attachment_id: temporaryId,
      file_name: file.name,
      media_type: file.type || "application/octet-stream",
      kind: attachmentKindOf(extension),
      size_bytes: file.size,
      parse_status: "pending",
      source: "upload",
      file,
      clientRequestId,
      uploading: true,
      progress: 0,
    };
    setAttachments((current) => [...current, placeholder]);
    const controller = new AbortController();
    uploadControllersRef.current.set(temporaryId, controller);
    void uploadAttachment(
      session.projectId,
      {
        userId: session.userId,
        tenantId: session.tenantId,
        file,
        clientRequestId,
        onProgress: (percent) => {
          setAttachments((current) =>
            current.map((item) =>
              item.attachment_id === temporaryId ? { ...item, progress: percent } : item,
            ),
          );
        },
      },
      controller.signal,
    )
      .then((uploaded) => {
        setAttachments((current) =>
          current.map((item) =>
            item.attachment_id === temporaryId
              ? { ...uploaded, source: "upload", file, clientRequestId, uploading: false, progress: 100 }
              : item,
          ),
        );
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        const text = error instanceof Error ? error.message : "上传失败，请重试。";
        setAttachments((current) =>
          current.map((item) =>
            item.attachment_id === temporaryId ? { ...item, uploading: false, uploadError: text } : item,
          ),
        );
        message.error(text);
      })
      .finally(() => {
        uploadControllersRef.current.delete(temporaryId);
      });
  };

  /** 批量添加：先做一次客户端预校验（类型/大小/数量），再逐个上传。 */
  const addFiles = (files: File[]) => {
    if (!canAttach || files.length === 0) return;
    const accepted: File[] = [];
    let lastError: string | null = null;
    let count = attachmentsRef.current.length;
    let totalBytes = attachmentsRef.current.reduce((sum, item) => sum + item.size_bytes, 0);
    for (const file of files) {
      const error = validateAttachmentFile(file, capabilities, {
        currentCount: count,
        currentTotalBytes: totalBytes,
      });
      if (error) {
        lastError = error;
        message.warning(error);
        continue;
      }
      accepted.push(file);
      count += 1;
      totalBytes += file.size;
    }
    // 部分文件被拒时也要把原因留在输入区，不能因为其他文件成功就清掉提示。
    setFileError(lastError);
    for (const file of accepted) startUpload(file);
  };

  const removeAttachment = (attachment: ComposerAttachment) => {
    uploadControllersRef.current.get(attachment.attachment_id)?.abort();
    uploadControllersRef.current.delete(attachment.attachment_id);
    if (preview?.attachment_id === attachment.attachment_id) setPreview(null);
    setAttachments((current) => current.filter((item) => item.attachment_id !== attachment.attachment_id));
    if (attachment.source === "upload" && !attachment.attachment_id.startsWith("uploading-") && attachment.status === "staged") {
      void deleteAttachment(attachment.attachment_id, { userId: session.userId, tenantId: session.tenantId }).catch(() => undefined);
    }
  };

  const retryUpload = (attachment: ComposerAttachment) => {
    if (!attachment.file) return;
    removeAttachment(attachment);
    addFiles([attachment.file]);
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
      tenantId: session.tenantId,
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
    if (files.length > 0) addFiles(files);
  };

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (!canAttach) return;
    const files = Array.from(event.clipboardData?.files ?? []);
    if (files.length === 0) return;
    event.preventDefault();
    addFiles(files);
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

  return (
    <div className="composer-wrap">
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
            <Icon name="plus" size={18} />松开即可添加附件
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
        {attachments.length > 0 ? (
          <div className="composer-attachments" aria-label="当前消息附件">
            {attachments.map((attachment) => {
              const remote = !attachment.attachment_id.startsWith("uploading-");
              const url = remote
                ? attachmentContentUrl(attachment.attachment_id, {
                    userId: session.userId,
                    tenantId: session.tenantId,
                  })
                : "";
              const failed = Boolean(attachment.uploadError) || attachment.parse_status === "failed" || attachment.parseTimedOut;
              return (
                <div className={`composer-attachment ${failed ? "has-error" : ""}`} key={attachment.attachment_id}>
                  {attachment.kind === "image" && url ? (
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
                      <span className="attachment-badge">
                        {attachment.kind === "image" ? "IMG" : attachmentBadge(attachment.file_name)}
                      </span>
                    </span>
                  )}
                  <span className="composer-attachment-body">
                    <span className="composer-attachment-name" title={attachment.file_name}>{attachment.file_name}</span>
                    <span className="composer-attachment-status">{statusText(attachment)}</span>
                    {attachment.uploading && attachment.progress ? (
                      <span
                        className="composer-attachment-progress"
                        role="progressbar"
                        aria-label={`${attachment.file_name} 上传进度`}
                        aria-valuenow={attachment.progress}
                        aria-valuemin={0}
                        aria-valuemax={100}
                      >
                        <span style={{ width: `${attachment.progress}%` }} />
                      </span>
                    ) : null}
                  </span>
                  {attachment.uploadError ? (
                    <button type="button" onClick={() => retryUpload(attachment)}>重试</button>
                  ) : null}
                  {remote && !attachment.uploading && (attachment.parse_status === "failed" || attachment.parseTimedOut) ? (
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
          loading={session.busy || session.conversationCreating}
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
              <button
                type="button"
                className="attachment-button"
                aria-label="添加附件"
                title="添加附件，也可拖拽文件或粘贴图片"
                disabled={!canAttach}
                onClick={() => fileInputRef.current?.click()}
              >
                <Icon name="plus" size={18} />
              </button>
              <input
                ref={fileInputRef}
                className="attachment-input"
                type="file"
                multiple
                accept={acceptExtensions}
                aria-label="选择附件"
                onChange={(event) => {
                  const files = Array.from(event.target.files ?? []);
                  event.target.value = "";
                  if (files.length > 0) addFiles(files);
                }}
              />
              {modelOptions.length > 0 ? (
                <Select
                  className="model-picker"
                  aria-label="选择模型"
                  value={selectedModelId || undefined}
                  disabled={inputDisabled || session.conversationCreating}
                  title="模型选择从下一条消息生效"
                  options={modelOptions.map((option) => ({
                    key: option.id,
                    value: option.id,
                    label: option.model,
                    disabled: !option.available,
                  }))}
                  onChange={(modelId) => session.selectModel?.(modelId)}
                />
              ) : null}
              <button
                className="send-button"
                type="button"
                onClick={submit}
                disabled={sendDisabled}
                aria-label={sendLabel}
                aria-busy={session.busy || session.conversationCreating}
                title={sendLabel}
              >
                <Icon
                  name={sendIcon}
                  size={18}
                  className={sendIcon === "loader-circle" ? "send-arrow mc-icon-spin" : "send-arrow"}
                />
              </button>
            </div>
          }
        />
      </div>
      {hasUnsupportedImage ? <div className="composer-attachment-warning" role="alert">当前模型不支持图片附件，请切换模型。</div> : null}
      {fileError ? <div className="composer-attachment-warning" role="alert">{fileError}</div> : null}
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
            tenantId: session.tenantId,
          })}
          alt={preview.file_name}
          onClose={() => setPreview(null)}
        />
      ) : null}
    </div>
  );
}
