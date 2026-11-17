import { useSyncExternalStore } from "react";

import { readStorage, writeStorage } from "../state/storage";

export type TtsVoice = "stella" | "tide";

export const TTS_VOICES: { id: TtsVoice; label: string }[] = [
  { id: "stella", label: "海星（女声）" },
  { id: "tide", label: "暗潮（男声）" },
];

export function ttsVoiceKey(userId: string): string {
  return `melonclaw.tts_voice.${userId}`;
}

export function readTtsVoice(userId: string): TtsVoice {
  return readStorage(ttsVoiceKey(userId)) === "tide" ? "tide" : "stella";
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setTtsVoice(userId: string, voice: TtsVoice): void {
  writeStorage(ttsVoiceKey(userId), voice);
  listeners.forEach((listener) => listener());
}

export function useTtsVoice(userId: string): TtsVoice {
  return useSyncExternalStore(subscribe, () => readTtsVoice(userId), () => "stella");
}
