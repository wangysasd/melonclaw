import { App } from "antd";
import { useEffect, useState } from "react";

import { toggleReadAloud, useReadAloud } from "../lib/readAloud";
import { readTtsVoice, setTtsVoice, TTS_VOICES, useTtsVoice } from "../lib/ttsVoice";
import type { ChatMessage } from "../state/chatStream";
import { Icon } from "./Icon";
import { ReadingBars } from "./ReadingBars";

/** 一条助手回复的朗读按钮和音色菜单。音色切换不打断正在播放的内容。 */
export function ReadAloudControls({
  message,
  conversationId,
  userId,
}: {
  message: ChatMessage;
  conversationId: string;
  userId: string;
}) {
  const { message: toast } = App.useApp();
  const playback = useReadAloud();
  const voice = useTtsVoice(userId);
  const status = playback.messageId === message.id ? playback.status : "idle";
  const [open, setOpen] = useState(false);
  const voiceLabel = TTS_VOICES.find((item) => item.id === voice)?.label ?? "海星（女声）";

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const label = status === "idle" ? "朗读" : "停止朗读";
  return (
    <div className="read-aloud-controls">
      <button
        type="button"
        className={`copy-button${status === "idle" ? "" : " is-reading"}`}
        aria-label={label}
        aria-busy={status === "loading" || undefined}
        onClick={() => {
          toggleReadAloud({
            messageId: message.id,
            conversationId,
            voiceId: readTtsVoice(userId),
            onError: (error) => {
              toast.error(error instanceof Error ? error.message : "朗读失败，请稍后再试");
            },
          });
        }}
      >
        {status === "idle"
          ? <Icon name="volume-2" size={14} />
          : <ReadingBars mode={status === "loading" ? "loading" : "playing"} />}
        <span>{label}</span>
      </button>
      <div className="tts-voice">
        <button
          type="button"
          className="copy-button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-label={`音色 ${voiceLabel}`}
          onClick={() => setOpen((current) => !current)}
        >
          <Icon name="chevron-down" size={14} />
        </button>
        {open ? (
          <ul className="tts-voice-menu" role="listbox" aria-label="朗读音色">
            {TTS_VOICES.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={item.id === voice}
                  className="copy-button"
                  onClick={() => {
                    setTtsVoice(userId, item.id);
                    setOpen(false);
                  }}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
