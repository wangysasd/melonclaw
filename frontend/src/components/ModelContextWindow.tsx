import { useState } from "react";
import { App, Button, Checkbox, InputNumber } from "antd";

import { updateModel } from "../api/client";
import type { ManageableModel } from "../types/api";

export const DEFAULT_CONTEXT_WINDOW = 1_000_000;
const MAX_CONTEXT_WINDOW = 2_147_483_647;

export function ContextWindowInput({ value, onChange }: {
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  return <label className="provider-field">上下文窗口（tokens）
    <InputNumber<number | string> aria-label="上下文窗口（tokens）" value={value} min={1} max={MAX_CONTEXT_WINDOW}
      precision={0} step={1000}
      formatter={(value) => String(value ?? "").replace(/\B(?=(\d{3})+(?!\d))/g, ",")}
      parser={(value) => (value ?? "").replace(/,/g, "")}
      onChange={(value) => onChange(value === null ? null : Number(value))}
      style={{ width: "100%" }} />
  </label>;
}

export function ChatModelSettingsEditor({ model, userId, onCancel, onSaved }: {
  model: ManageableModel;
  userId: string;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [contextWindow, setContextWindow] = useState(model.context_window);
  const [supportsImageInput, setSupportsImageInput] = useState(model.input_modalities.includes("image"));
  const [saving, setSaving] = useState(false);
  const valid = contextWindow !== null && Number.isInteger(contextWindow)
    && contextWindow > 0 && contextWindow <= MAX_CONTEXT_WINDOW;
  const save = async () => {
    if (!valid || saving) return;
    setSaving(true);
    try {
      await updateModel(model.model_key, { userId, contextWindow, supportsImageInput });
      message.success("模型设置已更新");
      onSaved();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="chat-model-settings" onSubmit={(event) => {
      event.preventDefault();
      void save();
    }}>
      <div className="chat-model-settings-fields">
        <ContextWindowInput value={contextWindow} onChange={setContextWindow} />
        <Checkbox checked={supportsImageInput} onChange={(event) => setSupportsImageInput(event.target.checked)}>
          支持图片输入
        </Checkbox>
      </div>
      <div className="chat-model-actions">
        <Button onClick={onCancel} disabled={saving}>取消</Button>
        <Button type="primary" htmlType="submit" loading={saving} disabled={!valid}>保存</Button>
      </div>
    </form>
  );
}
