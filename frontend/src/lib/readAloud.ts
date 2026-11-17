import { useSyncExternalStore } from "react";

import { ApiError } from "../api/client";
import { synthesizeSpeech } from "../api/speech";
import type { TtsVoice } from "./ttsVoice";

export const TTS_SAMPLE_RATE = 24000;
export const START_DELAY = 0.1;

export type ReadAloudStatus = "idle" | "loading" | "playing";

export interface ReadAloudSnapshot {
  messageId: string | null;
  status: ReadAloudStatus;
}

interface PcmBuffer {
  duration: number;
  copyToChannel(source: Float32Array, channel: number): void;
}

interface PcmSource {
  buffer: PcmBuffer | null;
  connect(destination: unknown): void;
  start(when?: number): void;
  onended: ((ev: Event) => void) | null;
}

export interface PcmPlayback {
  currentTime: number;
  destination: unknown;
  createBuffer(channels: number, length: number, sampleRate: number): PcmBuffer;
  createBufferSource(): PcmSource;
  close(): void | Promise<void>;
  resume(): void | Promise<void>;
}

export interface ReadAloudDeps {
  createContext: () => PcmPlayback;
  synthesize: (
    input: { conversationId: string; messageId: string; voiceId: TtsVoice },
    signal: AbortSignal,
  ) => Promise<ReadableStream<Uint8Array>>;
}

const listeners = new Set<() => void>();
let snapshot: ReadAloudSnapshot = { messageId: null, status: "idle" };
let generation = 0;
let current: { controller: AbortController; context: PcmPlayback; token: number } | null = null;

function publish(next: ReadAloudSnapshot): void {
  if (snapshot.messageId === next.messageId && snapshot.status === next.status) return;
  snapshot = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getReadAloudSnapshot(): ReadAloudSnapshot {
  return snapshot;
}

export function useReadAloud(): ReadAloudSnapshot {
  return useSyncExternalStore(subscribe, getReadAloudSnapshot, getReadAloudSnapshot);
}

export function canReadAloud(message: {
  role: string;
  content: string;
  status: string | null;
  optimistic?: boolean;
}): boolean {
  return message.role === "assistant"
    && message.content.trim().length > 0
    && !message.optimistic
    && (message.status === "completed"
      || message.status === "failed"
      || message.status === "cancelled"
      || message.status === "interrupted");
}

export function stopReadAloud(): void {
  generation += 1;
  const running = current;
  current = null;
  running?.controller.abort();
  void running?.context.close();
  publish({ messageId: null, status: "idle" });
}

function finish(token: number): void {
  if (token !== generation) return;
  stopReadAloud();
}

function isAbort(error: unknown, signal: AbortSignal): boolean {
  return signal.aborted || (error instanceof DOMException && error.name === "AbortError");
}

/** 把裸 PCM 排进播放时间线。奇数字节留给下一块。 */
export async function playPcmStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  context: PcmPlayback,
  signal: AbortSignal,
  onPlaying: () => void,
): Promise<"done" | "empty"> {
  let rest = new Uint8Array(0);
  let nextTime = 0;
  let lastEnded: Promise<void> | null = null;
  try {
    for (;;) {
      if (signal.aborted) return "done";
      const { done, value } = await reader.read();
      if (signal.aborted) return "done";
      if (done) break;
      if (!value?.byteLength) continue;
      const bytes = new Uint8Array(rest.length + value.length);
      bytes.set(rest, 0);
      bytes.set(value, rest.length);
      const count = bytes.length >> 1;
      rest = bytes.slice(count * 2);
      if (count === 0) continue;
      const samples = new Float32Array(count);
      const view = new DataView(bytes.buffer, bytes.byteOffset, count * 2);
      for (let index = 0; index < count; index += 1) {
        samples[index] = view.getInt16(index * 2, true) / 0x8000;
      }
      const buffer = context.createBuffer(1, count, TTS_SAMPLE_RATE);
      buffer.copyToChannel(samples, 0);
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      const first = lastEnded === null;
      lastEnded = new Promise<void>((resolve) => {
        source.onended = () => {
          source.onended = null;
          resolve();
        };
      });
      nextTime = Math.max(nextTime, context.currentTime + (first ? START_DELAY : 0));
      source.start(nextTime);
      nextTime += buffer.duration;
      if (first) onPlaying();
    }
  } catch (error) {
    if (isAbort(error, signal)) return "done";
    throw error;
  }
  if (!lastEnded) return "empty";
  if (signal.aborted) return "done";
  await new Promise<void>((resolve) => {
    const finish = () => {
      signal.removeEventListener("abort", finish);
      resolve();
    };
    signal.addEventListener("abort", finish, { once: true });
    void lastEnded.then(finish);
  });
  return "done";
}

const defaultDeps: ReadAloudDeps = {
  createContext: () => new AudioContext({ sampleRate: TTS_SAMPLE_RATE }),
  synthesize: (input, signal) => synthesizeSpeech(input, signal),
};

/**
 * 开始或停止朗读。AudioContext 在返回前同步创建，满足浏览器的用户手势要求。
 */
export function toggleReadAloud(
  input: {
    messageId: string;
    conversationId: string;
    voiceId: TtsVoice;
    onError: (error: unknown) => void;
  },
  deps: ReadAloudDeps = defaultDeps,
): void {
  const activeId = snapshot.messageId;
  stopReadAloud();
  if (activeId === input.messageId) return;
  const context = deps.createContext();
  void context.resume();
  const controller = new AbortController();
  const token = generation;
  current = { controller, context, token };
  publish({ messageId: input.messageId, status: "loading" });
  void runPlayback(input, deps, context, controller, token);
}

async function runPlayback(
  input: {
    messageId: string;
    conversationId: string;
    voiceId: TtsVoice;
    onError: (error: unknown) => void;
  },
  deps: ReadAloudDeps,
  context: PcmPlayback,
  controller: AbortController,
  token: number,
): Promise<void> {
  let audible = false;
  try {
    const stream = await deps.synthesize(
      {
        conversationId: input.conversationId,
        messageId: input.messageId,
        voiceId: input.voiceId,
      },
      controller.signal,
    );
    if (controller.signal.aborted || token !== generation) return;
    const outcome = await playPcmStream(stream.getReader(), context, controller.signal, () => {
      audible = true;
      if (token === generation) publish({ messageId: input.messageId, status: "playing" });
    });
    if (controller.signal.aborted || token !== generation) return;
    if (outcome === "empty") {
      finish(token);
      input.onError(new Error("朗读失败，请稍后再试"));
      return;
    }
    finish(token);
  } catch (error) {
    if (isAbort(error, controller.signal) || token !== generation) return;
    finish(token);
    if (audible || !(error instanceof ApiError)) {
      input.onError(new Error("朗读失败，请稍后再试"));
    } else {
      input.onError(error);
    }
  }
}
