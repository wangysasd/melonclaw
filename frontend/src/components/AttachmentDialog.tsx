import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { Modal } from "antd";

import { Icon } from "./Icon";
import {
  attachmentBadge,
  attachmentKindOf,
  describeAttachmentLimits,
  extensionOf,
  formatBytes,
  validateAttachmentFile,
} from "../lib/attachmentFiles";
import { deleteAttachment, uploadAttachment } from "../api/client";
import type { AttachmentCapabilities, AttachmentSummary } from "../types/api";

/** 弹窗内暂存、尚未提交到输入区的附件。 */
export type StagedAttachment = AttachmentSummary & {
  file: File;
  clientRequestId: string;
  uploading?: boolean;
  uploadError?: string | null;
  progress?: number;
};

export interface AttachmentDialogProps {
  open: boolean;
  /** 每次打开时递增：用来重置列表并消费 initialFiles。 */
  sessionId: number;
  /** 由输入区拖拽/粘贴带进来的文件；打开弹窗后自动上传。 */
  initialFiles?: File[];
  capabilities: AttachmentCapabilities;
  userId: string;
  tenantId: string;
  projectId: string;
  /** 输入区已确认的附件，用于合并计算数量与总大小上限。 */
  existingCount: number;
  existingTotalBytes: number;
  onClose: () => void;
  onConfirm: (items: StagedAttachment[]) => void;
}

export function attachmentStatusText(item: StagedAttachment): string {
  if (item.uploading) {
    return item.progress ? `上传中 ${item.progress}%` : "上传中…";
  }
  if (item.uploadError) return "上传失败";
  return "已上传";
}

/**
 * 附件弹窗：上传、移除、取消或统一确认。
 *
 * 提交语义对齐两阶段协议：选择文件时立刻上传成 staged 附件，点「添加附件」
 * 才交给输入区（发送消息时才会真正绑定）；点「取消」会把本次上传的 staged
 * 附件删掉，不留在工作区里等 TTL。
 */
export function AttachmentDialog({
  open,
  sessionId,
  initialFiles,
  capabilities,
  userId,
  tenantId,
  projectId,
  existingCount,
  existingTotalBytes,
  onClose,
  onConfirm,
}: AttachmentDialogProps) {
  const [items, setItems] = useState<StagedAttachment[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const itemsRef = useRef<StagedAttachment[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const controllersRef = useRef(new Map<string, AbortController>());
  const dragDepthRef = useRef(0);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(
    () => () => {
      for (const controller of controllersRef.current.values()) controller.abort();
      controllersRef.current.clear();
    },
    [],
  );

  const startUpload = (file: File) => {
    const clientRequestId = crypto.randomUUID();
    const temporaryId = `uploading-${clientRequestId}`;
    const placeholder: StagedAttachment = {
      attachment_id: temporaryId,
      file_name: file.name,
      media_type: file.type || "application/octet-stream",
      kind: attachmentKindOf(extensionOf(file.name)),
      size_bytes: file.size,
      parse_status: "pending",
      file,
      clientRequestId,
      uploading: true,
      progress: 0,
    };
    setItems((current) => [...current, placeholder]);
    const controller = new AbortController();
    controllersRef.current.set(temporaryId, controller);
    void uploadAttachment(
      projectId,
      {
        userId,
        tenantId,
        file,
        clientRequestId,
        onProgress: (percent) => {
          setItems((current) =>
            current.map((item) =>
              item.attachment_id === temporaryId ? { ...item, progress: percent } : item,
            ),
          );
        },
      },
      controller.signal,
    )
      .then((uploaded) => {
        setItems((current) =>
          current.map((item) =>
            item.attachment_id === temporaryId
              ? { ...uploaded, file, clientRequestId, uploading: false, progress: 100 }
              : item,
          ),
        );
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        const text = error instanceof Error ? error.message : "上传失败，请重试。";
        setItems((current) =>
          current.map((item) =>
            item.attachment_id === temporaryId
              ? { ...item, uploading: false, uploadError: text }
              : item,
          ),
        );
      })
      .finally(() => {
        controllersRef.current.delete(temporaryId);
      });
  };

  /** 批量添加：先按能力清单预校验（类型/大小/数量），通过后再逐个上传。 */
  const addFiles = (files: File[]) => {
    if (files.length === 0) return;
    const accepted: File[] = [];
    let lastError: string | null = null;
    let count = existingCount + itemsRef.current.length;
    let totalBytes =
      existingTotalBytes +
      itemsRef.current.reduce((sum, item) => sum + item.size_bytes, 0);
    for (const file of files) {
      const error = validateAttachmentFile(file, capabilities, {
        currentCount: count,
        currentTotalBytes: totalBytes,
      });
      if (error) {
        lastError = error;
        continue;
      }
      accepted.push(file);
      count += 1;
      totalBytes += file.size;
    }
    // 部分文件被拒时也要保留原因，不能因为其他文件成功就清掉提示。
    setFileError(lastError);
    for (const file of accepted) startUpload(file);
  };

  const removeItem = (item: StagedAttachment) => {
    controllersRef.current.get(item.attachment_id)?.abort();
    controllersRef.current.delete(item.attachment_id);
    setItems((current) =>
      current.filter((entry) => entry.attachment_id !== item.attachment_id),
    );
    if (!item.attachment_id.startsWith("uploading-") && !item.uploadError) {
      void deleteAttachment(item.attachment_id, { userId, tenantId }).catch(
        () => undefined,
      );
    }
  };

  const purgeStaged = () => {
    for (const controller of controllersRef.current.values()) controller.abort();
    controllersRef.current.clear();
    for (const item of itemsRef.current) {
      if (!item.attachment_id.startsWith("uploading-") && !item.uploadError) {
        void deleteAttachment(item.attachment_id, { userId, tenantId }).catch(
          () => undefined,
        );
      }
    }
  };

  const resetLocal = () => {
    itemsRef.current = [];
    setItems([]);
    setFileError(null);
    setDragging(false);
    dragDepthRef.current = 0;
  };

  // 每次打开都从空白开始；拖拽/粘贴带进来的文件在这里自动上传。
  useEffect(() => {
    if (!open) return;
    resetLocal();
    if (initialFiles && initialFiles.length > 0) addFiles(initialFiles);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const handleCancel = () => {
    purgeStaged();
    resetLocal();
    onClose();
  };

  const ready = items.filter((item) => !item.uploading && !item.uploadError);
  const uploading = items.some((item) => item.uploading);
  const confirmDisabled = uploading || ready.length === 0;
  const acceptExtensions = capabilities.items.map((item) => item.extension).join(",");

  const handleConfirm = () => {
    if (confirmDisabled) return;
    const confirmed = ready;
    resetLocal();
    onConfirm(confirmed);
    onClose();
  };

  const handleDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragging(true);
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragging(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length > 0) addFiles(files);
  };

  const handleDropzoneKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    inputRef.current?.click();
  };

  return (
    <Modal
      open={open}
      title="添加附件"
      okText="确认添加附件"
      cancelText="取消"
      centered
      width={520}
      onOk={handleConfirm}
      onCancel={handleCancel}
      okButtonProps={{ disabled: confirmDisabled, autoInsertSpace: false }}
      cancelButtonProps={{ autoInsertSpace: false }}
      className="attachment-dialog"
    >
      <div
        className={`attachment-dropzone ${dragging ? "is-dragging" : ""}`}
        role="button"
        tabIndex={0}
        aria-label="点击选择文件，或把文件拖到这里"
        onClick={() => inputRef.current?.click()}
        onKeyDown={handleDropzoneKeyDown}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <Icon name="plus" size={20} className="dropzone-icon" />
        <p className="dropzone-title">点击选择文件，或把文件拖到这里</p>
        <p className="dropzone-desc">{describeAttachmentLimits(capabilities)}</p>
      </div>
      <input
        ref={inputRef}
        className="attachment-input"
        type="file"
        multiple
        accept={acceptExtensions}
        aria-label="选择附件"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          addFiles(files);
        }}
      />

      {fileError ? (
        <p className="attachment-dialog-alert" role="alert">
          {fileError}
        </p>
      ) : null}

      {items.length > 0 ? (
        <div className="attachment-dialog-list" aria-label="待添加附件">
          {items.map((item) => {
            const failed = Boolean(item.uploadError);
            return (
              <div
                className={`attachment-dialog-item ${failed ? "has-error" : ""}`}
                key={item.attachment_id}
              >
                <span className="attachment-file-icon" aria-hidden="true">
                  <Icon name="file-text" size={18} />
                  <span className="attachment-badge">{attachmentBadge(item.file_name)}</span>
                </span>
                <div className="attachment-dialog-body">
                  <div className="attachment-dialog-name" title={item.file_name}>
                    {item.file_name}
                  </div>
                  <div className="attachment-dialog-meta">
                    <span className={failed ? "attachment-dialog-status has-error" : "attachment-dialog-status"}>
                      {attachmentStatusText(item)}
                    </span>
                    <span>{formatBytes(item.size_bytes)}</span>
                    {item.uploadError ? (
                      <span className="attachment-dialog-error">{item.uploadError}</span>
                    ) : null}
                  </div>
                  {item.uploading && item.progress ? (
                    <span
                      className="attachment-dialog-progress"
                      role="progressbar"
                      aria-label={`${item.file_name} 上传进度`}
                      aria-valuenow={item.progress}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <span style={{ width: `${item.progress}%` }} />
                    </span>
                  ) : null}
                </div>
                <button
                  type="button"
                  className="attachment-dialog-remove"
                  aria-label={`移除 ${item.file_name}`}
                  onClick={() => removeItem(item)}
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
    </Modal>
  );
}
