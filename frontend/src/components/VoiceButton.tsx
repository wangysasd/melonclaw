import type { VoiceInputStatus } from "../hooks/useVoiceInput";
import { Icon } from "./Icon";

export function VoiceButton({
  status,
  disabled = false,
  seconds,
  onClick,
  onPointerDown,
}: {
  status: VoiceInputStatus;
  disabled?: boolean;
  seconds: number;
  onClick: () => void;
  onPointerDown?: () => void;
}) {
  const recording = status === "recording";
  const label = disabled
    ? "非 HTTPS 页面无法使用语音输入"
    : recording
      ? "结束录音并转写"
      : status === "transcribing"
        ? "正在转写，点击取消"
        : "语音输入";
  return (
    <>
      {recording ? <span className="voice-timer">录音中 {seconds}s</span> : null}
      <button
        type="button"
        className={`voice-button${recording ? " is-recording" : ""}${disabled ? " is-disabled" : ""}`}
        aria-label={label}
        title={label}
        aria-pressed={recording}
        aria-disabled={disabled || undefined}
        aria-busy={status === "transcribing"}
        onMouseDown={onPointerDown}
        onClick={() => {
          if (!disabled) onClick();
        }}
      >
        {recording ? (
          <span className="voice-active">
            <span className="voice-halo" />
            <span className="voice-core" />
            <Icon name="mic" size={16} className="voice-glyph" />
          </span>
        ) : status === "transcribing" ? (
          <Icon name="loader-circle" size={18} className="mc-icon-spin" />
        ) : (
          <Icon name="mic" size={18} />
        )}
      </button>
    </>
  );
}
