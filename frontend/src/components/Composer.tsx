import { useRef } from "react";
import { Select } from "antd";
import Sender from "@ant-design/x/es/sender";

import { Icon } from "./Icon";
import { useSession } from "../state/session";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (value: string) => void;
  disabled: boolean;
}

/** Sender 仅负责输入展示；模型快照与发送恢复仍由现有聊天流管理。 */
export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  const session = useSession();
  const composingRef = useRef(false);
  const submittingRef = useRef(false);

  let placeholder = "输入你想聊的事情…";
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
  const sendDisabled = inputDisabled || disabled || session.busy || session.runStatus === "waiting" || session.conversationCreating || !value.trim();
  const modelOptions = session.modelOptions ?? [];
  const selectedModelId =
    session.selectedModelId || modelOptions.find((item) => item.available)?.id || "";
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

  const submit = () => {
    if (sendDisabled || composingRef.current || submittingRef.current) return;
    submittingRef.current = true;
    try { onSend(value); } finally {
      queueMicrotask(() => { submittingRef.current = false; });
    }
  };

  return (
    <div className="composer-wrap">
      <div className="composer" onCompositionStartCapture={() => { composingRef.current = true; }}
        onCompositionEndCapture={() => { composingRef.current = false; }}>
        <Sender
          className="melon-sender"
          value={value}
          placeholder={placeholder}
          aria-label="输入内容"
          aria-describedby="composer-hint"
          disabled={inputDisabled}
          loading={session.busy || session.conversationCreating}
          onChange={(nextValue) => onChange(nextValue)}
          onSubmit={submit}
          submitType="enter"
          autoSize={{ minRows: 1, maxRows: 6 }}
          suffix={false}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || event.keyCode === 229 || composingRef.current) return false;
            if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {
              event.preventDefault();
              submit();
              return false;
            }
          }}
          footer={
            <div className="composer-bottom">
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
      <div className="composer-meta">
        <div className="composer-hint" id="composer-hint">
          <Icon name="message-circle" size={15} /> Enter 发送 · Shift + Enter 换行
        </div>
        <div className="composer-footnote">AI生成内容仅供参考。</div>
      </div>
    </div>
  );
}
