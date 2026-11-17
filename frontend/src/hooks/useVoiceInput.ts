import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { transcribeSpeech } from "../api/speech";
import { startRecording, type Recording } from "../lib/speechRecorder";

export type VoiceInputStatus = "idle" | "recording" | "transcribing";
export type VoiceNotice = "error" | "info" | "warning";

function recordingFailure(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      return "无法使用麦克风，请在浏览器设置中允许访问";
    }
    if (error.name === "NotFoundError") return "未检测到麦克风";
  }
  return "语音识别失败，请稍后再试";
}

export function useNarrowViewport(): boolean {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 768px)");
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return matches;
}

/**
 * 点击开始录音，再次点击结束并转写。转写中点击取消。
 * 聊天页隐藏或上下文变化时放弃当前录音和转写。
 */
export function useVoiceInput(options: {
  active: boolean;
  contextKey: string;
  onText: (text: string) => void;
  notify: (kind: VoiceNotice, text: string) => void;
}) {
  const [status, setStatus] = useState<VoiceInputStatus>("idle");
  const [seconds, setSeconds] = useState(0);
  const recordingRef = useRef<Recording | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const operationRef = useRef(0);
  const onTextRef = useRef(options.onText);
  const notifyRef = useRef(options.notify);
  onTextRef.current = options.onText;
  notifyRef.current = options.notify;
  const available = typeof window === "undefined" || window.isSecureContext;

  const cancelOperation = useCallback(() => {
    operationRef.current += 1;
    recordingRef.current?.cancel();
    recordingRef.current = null;
    controllerRef.current?.abort();
    controllerRef.current = null;
  }, []);

  const cancel = useCallback(() => {
    cancelOperation();
    setStatus("idle");
  }, [cancelOperation]);

  const finish = useCallback(async () => {
    const recording = recordingRef.current;
    if (!recording) {
      cancel();
      return;
    }
    const operation = operationRef.current;
    recordingRef.current = null;
    let controller: AbortController | null = null;
    try {
      let audio: Blob | null;
      try {
        audio = await recording.stop();
      } finally {
        recording.cancel();
      }
      if (operationRef.current !== operation || !audio) {
        if (operationRef.current === operation && !audio) {
          notifyRef.current("info", "未识别到语音内容");
        }
        return;
      }
      controller = new AbortController();
      controllerRef.current = controller;
      setStatus("transcribing");
      const transcript = await transcribeSpeech(audio, controller.signal);
      if (controller.signal.aborted || operationRef.current !== operation) return;
      const text = transcript.text.trim();
      if (text) onTextRef.current(text);
      else notifyRef.current("info", "未识别到语音内容");
    } catch (error) {
      if (operationRef.current !== operation || controller?.signal.aborted) return;
      if (error instanceof DOMException && error.name === "AbortError") return;
      const message = error instanceof Error && error.message.trim()
        ? error.message
        : "语音识别失败，请稍后再试";
      notifyRef.current("error", message);
    } finally {
      if (operationRef.current === operation) {
        controllerRef.current = null;
        setStatus("idle");
      }
    }
  }, [cancel]);

  const toggle = useCallback(async () => {
    if (!available || !options.active) return;
    if (status === "recording") {
      await finish();
      return;
    }
    if (status === "transcribing") {
      cancel();
      return;
    }
    const operation = ++operationRef.current;
    setStatus("recording");
    try {
      const recording = await startRecording(() => {
        if (operationRef.current !== operation) return;
        notifyRef.current("warning", "已达到单次录音时长上限");
        void finish();
      });
      if (operationRef.current !== operation) {
        recording.cancel();
        return;
      }
      recordingRef.current = recording;
    } catch (error) {
      if (operationRef.current === operation) {
        setStatus("idle");
        notifyRef.current("error", recordingFailure(error));
      }
    }
  }, [available, options.active, status, finish, cancel]);

  useEffect(() => {
    if (status !== "recording") return;
    const startedAt = Date.now();
    setSeconds(0);
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 500);
    return () => window.clearInterval(timer);
  }, [status]);

  useLayoutEffect(() => {
    setStatus("idle");
    return cancelOperation;
  }, [options.active, options.contextKey, cancelOperation]);

  const getAnalyser = useCallback(() => recordingRef.current?.analyser ?? null, []);
  return { status, seconds, getAnalyser, toggle, cancel, available };
}
