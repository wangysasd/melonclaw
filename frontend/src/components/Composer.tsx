import { useLayoutEffect, useRef, type FormEvent } from "react";

import { Icon } from "./Icon";
import { useSession } from "../state/session";

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (value: string) => void;
  disabled: boolean;
}

/** 输入区：恢复旧版 textarea/form 布局，Enter 发送、Shift+Enter 换行。 */
export function Composer({ value, onChange, onSend, disabled }: ComposerProps) {
  const session = useSession();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);

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
  const sendDisabled = inputDisabled || disabled || !value.trim();
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

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }, [value]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!sendDisabled) onSend(value);
  };

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={submit}>
        <textarea
          ref={inputRef}
          rows={1}
          value={value}
          placeholder={placeholder}
          aria-label="输入内容"
          aria-describedby="composer-hint"
          disabled={inputDisabled}
          onChange={(event) => onChange(event.currentTarget.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing &&
              !composingRef.current
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className="composer-bottom">
          {modelOptions.length > 0 ? (
            <label className="model-picker">
              <span className="sr-only">选择模型</span>
              <select
                aria-label="选择模型"
                value={selectedModelId}
                disabled={inputDisabled || session.conversationCreating}
                title="模型选择从下一条消息生效"
                onChange={(event) => session.selectModel?.(event.currentTarget.value)}
              >
                {modelOptions.map((option) => (
                  <option key={option.id} value={option.id} disabled={!option.available}>
                    {option.model}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button
            className="send-button"
            type="submit"
            disabled={sendDisabled}
            aria-label={sendLabel}
            aria-busy={session.busy || session.conversationCreating}
          >
            <Icon name="arrow-up" size={18} className="send-arrow" />
          </button>
        </div>
      </form>
      <div className="composer-meta">
        <div className="composer-hint" id="composer-hint">
          <Icon name="message-circle" size={15} /> Enter 发送 · Shift + Enter 换行
        </div>
        <div className="composer-footnote">AI生成内容仅供参考。</div>
      </div>
    </div>
  );
}
