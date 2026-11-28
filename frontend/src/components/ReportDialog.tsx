import { Button, Modal } from "antd";
import { useState } from "react";
import { submitReport, type ReportSubmitResult } from "../api/reports";
import { FeedbackForm, type FeedbackValues } from "./FeedbackForm";

export function ReportDialog({ onClose, onSubmitted, onViewProgress }: {
  onClose: () => void;
  onSubmitted: () => void;
  onViewProgress: (code: string) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<ReportSubmitResult | null>(null);

  async function submit(values: FeedbackValues) {
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      setSaved(await submitReport({ ...values, target: { kind: "general" } }));
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  return <Modal
    title={saved ? "反馈已提交" : "提交反馈"}
    open
    onCancel={() => { if (!submitting) onClose(); }}
    footer={saved ? [
      <Button key="progress" type="primary" onClick={() => onViewProgress(saved.public_code)}>查看进度</Button>,
      <Button key="close" onClick={onClose}>关闭</Button>,
    ] : null}
    mask={{ closable: !submitting }}
    keyboard={!submitting}
  >
    {saved ? <div role="status">
      <p>工单编号：<strong>{saved.public_code}</strong></p>
      <p>可在「意见反馈」查看处理进度。</p>
    </div> : <FeedbackForm onSubmit={(values) => void submit(values)} submitting={submitting} error={error} />}
  </Modal>;
}
