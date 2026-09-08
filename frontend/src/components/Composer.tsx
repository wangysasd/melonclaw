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
  } else if (session.busy) {
    placeholder = "助手正在处理，结果会实时出现…";
  }

  const canUse =
    session.contextReady &&
    session.status?.status === "ready" &&
    session.projects.length > 0;

  const inputDisabled = !canUse || disabled;
  const sendDisabled = inputDisabled || !value.trim();
  const sendLabel = session.conversationCreating
    ? "准备中"
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
          <button
            className="send-button"
            type="submit"
            disabled={sendDisabled}
            aria-busy={session.busy || session.conversationCreating}
          >
            <span>{sendLabel}</span>
            <Icon
              name="arrow-up"
              size={17}
              className="send-arrow"
              style={{ backgroundColor: "rgba(255, 255, 255, 0.18)" }}
            />
          </button>
        </div>
      </form>
      <div className="composer-meta">
        <div className="composer-hint">
          <Icon name="message-circle" size={15} /> Enter 发送 · Shift + Enter 换行
        </div>
        <div className="composer-footnote">AI生成内容仅供参考。</div>
      </div>
    </div>
  );
}
