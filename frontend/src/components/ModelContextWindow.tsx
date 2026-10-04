import { useState } from "react";
import { App, Button, InputNumber, Modal } from "antd";

import { updateModel } from "../api/client";
import type { ManageableModel } from "../types/api";

export const DEFAULT_CONTEXT_WINDOW = 1_000_000;
const MAX_CONTEXT_WINDOW = 2_147_483_647;

export function ContextWindowInput({ value, onChange }: {
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  return <label className="provider-field">上下文窗口（tokens）
    <InputNumber aria-label="上下文窗口（tokens）" value={value} min={1} max={MAX_CONTEXT_WINDOW}
      precision={0} step={1000} onChange={onChange} style={{ width: "100%" }} />
  </label>;
}

export function ModelContextWindowEditor({ model, userId, onChanged }: {
  model: ManageableModel;
  userId: string;
  onChanged: () => void;
}) {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<number | null>(model.context_window);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    if (!value || value <= 0 || !Number.isInteger(value) || value > MAX_CONTEXT_WINDOW) return;
    setSaving(true);
    try {
      await updateModel(model.model_key, { userId, contextWindow: value });
      setOpen(false);
      message.success("上下文窗口已更新");
      onChanged();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };
  return <>
    <Button size="small" onClick={() => { setValue(model.context_window); setOpen(true); }}>上下文窗口</Button>
    <Modal title={`${model.display_name} · 上下文窗口`} open={open} onCancel={() => setOpen(false)}
      onOk={() => void save()} confirmLoading={saving} okButtonProps={{ disabled: !value || value <= 0 || !Number.isInteger(value) || value > MAX_CONTEXT_WINDOW }}
      okText="保存" cancelText="取消">
      <ContextWindowInput value={value} onChange={setValue} />
      <p className="resource-hint">填写该模型实际支持的 token 窗口。压缩按系统配置比例触发；1M 表示 1,000,000 tokens。</p>
    </Modal>
  </>;
}
