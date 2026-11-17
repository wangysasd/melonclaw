import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReadAloudControls } from "../src/components/ReadAloudControls";
import type { ChatMessage } from "../src/hooks/useChatStream";
import {
  canReadAloud,
  getReadAloudSnapshot,
  playPcmStream,
  START_DELAY,
  stopReadAloud,
  toggleReadAloud,
  TTS_SAMPLE_RATE,
  type PcmPlayback,
} from "../src/lib/readAloud";
import { readTtsVoice, setTtsVoice, ttsVoiceKey } from "../src/lib/ttsVoice";

const speechCalls = vi.hoisted(() => ({ voices: [] as string[], signals: [] as AbortSignal[] }));
vi.mock("../src/api/speech", () => ({
  transcribeSpeech: vi.fn(),
  synthesizeSpeech: (input: { voiceId: string }, signal: AbortSignal) => {
    speechCalls.voices.push(input.voiceId);
    speechCalls.signals.push(signal);
    return new Promise(() => undefined);
  },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() } }),
    },
  };
});

function message(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: "a1",
    role: "assistant",
    content: "可以朗读",
    status: "completed",
    markdown: true,
    events: [],
    assistantSteps: [],
    phases: [],
    artifacts: [],
    ...overrides,
  };
}

interface FakePlayback extends PcmPlayback {
  starts: number[];
  samples: number[][];
  sources: { onended: (() => void) | null }[];
  closed: boolean;
  resumed: boolean;
}

function fakePlayback(): FakePlayback {
  const playback = {
    currentTime: 0,
    destination: {},
    starts: [] as number[],
    samples: [] as number[][],
    sources: [] as { onended: (() => void) | null }[],
    closed: false,
    resumed: false,
    createBuffer(_channels: number, length: number) {
      return {
        duration: length / TTS_SAMPLE_RATE,
        copyToChannel(source: Float32Array) {
          playback.samples.push(Array.from(source));
        },
      };
    },
    createBufferSource() {
      const source = {
        buffer: null,
        connect() { /* unused */ },
        start(when = 0) { playback.starts.push(when); },
        onended: null as (() => void) | null,
      };
      playback.sources.push(source);
      return source;
    },
    close() { playback.closed = true; },
    resume() {
      playback.resumed = true;
      return Promise.resolve();
    },
  };
  return playback;
}

function streamOf(...chunks: number[][]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(new Uint8Array(chunk)));
      controller.close();
    },
  });
}

afterEach(() => {
  stopReadAloud();
  localStorage.clear();
  speechCalls.voices.length = 0;
  speechCalls.signals.length = 0;
});

describe("tts voice", () => {
  it("falls back to stella and stores the next choice per user", () => {
    localStorage.setItem(ttsVoiceKey("u1"), "other");
    expect(readTtsVoice("u1")).toBe("stella");
    setTtsVoice("u1", "tide");
    expect(localStorage.getItem(ttsVoiceKey("u1"))).toBe("tide");
    expect(readTtsVoice("u1")).toBe("tide");
    expect(readTtsVoice("u2")).toBe("stella");
  });
});

describe("read aloud visibility", () => {
  it("shows only finished assistant answers", () => {
    expect(canReadAloud(message())).toBe(true);
    for (const status of ["failed", "cancelled", "interrupted"] as const) {
      expect(canReadAloud(message({ status }))).toBe(true);
    }
    expect(canReadAloud(message({ role: "user" }))).toBe(false);
    expect(canReadAloud(message({ status: "pending" }))).toBe(false);
    expect(canReadAloud(message({ status: "streaming" }))).toBe(false);
    expect(canReadAloud(message({ content: "  " }))).toBe(false);
    expect(canReadAloud(message({ optimistic: true }))).toBe(false);
  });
});

describe("pcm playback", () => {
  it("finishes when the final audio plays before the stream closes", async () => {
    const playback = fakePlayback();
    let closeStream = () => undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0, 0]));
        closeStream = () => controller.close();
      },
    });
    const done = playPcmStream(stream.getReader(), playback, new AbortController().signal, vi.fn());
    await act(async () => { await Promise.resolve(); });
    const source = playback.sources[0];
    expect(source.onended).toBeTypeOf("function");
    source.onended?.();
    closeStream();
    await expect(done).resolves.toBe("done");
  });

  it("carries an odd byte and schedules from the first delay", async () => {
    const playback = fakePlayback();
    const playing = vi.fn();
    const done = playPcmStream(
      streamOf([0x00, 0x40, 0x01], [0x00]).getReader(),
      playback,
      new AbortController().signal,
      playing,
    );
    await act(async () => { await Promise.resolve(); });
    expect(playing).toHaveBeenCalledOnce();
    expect(playback.starts[0]).toBeCloseTo(START_DELAY);
    expect(playback.starts[1]).toBeCloseTo(START_DELAY + 1 / TTS_SAMPLE_RATE);
    expect(playback.samples[0]?.[0]).toBeCloseTo(0x4000 / 0x8000);
    expect(playback.samples[1]?.[0]).toBeCloseTo(1 / 0x8000);
    playback.sources.at(-1)?.onended?.();
    await done;
  });

  it("continues from the current time when playback is ahead of the network", async () => {
    const playback = fakePlayback();
    let release = () => undefined;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0x00, 0x00]));
        return new Promise<void>((resolve) => {
          release = () => {
            playback.currentTime = 5;
            controller.enqueue(new Uint8Array([0x00, 0x00]));
            controller.close();
            resolve();
          };
        });
      },
    });
    const done = playPcmStream(stream.getReader(), playback, new AbortController().signal, vi.fn());
    await act(async () => { await Promise.resolve(); });
    release();
    await act(async () => { await Promise.resolve(); });
    expect(playback.starts[1]).toBe(5);
    playback.sources.at(-1)?.onended?.();
    await done;
  });

  it("reports an empty stream and stops after a later read failure", async () => {
    const playback = fakePlayback();
    await expect(playPcmStream(streamOf().getReader(), playback, new AbortController().signal, vi.fn())).resolves.toBe("empty");
    const broken = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([0x00, 0x00]));
        controller.error(new Error("cut"));
      },
    });
    const failed = fakePlayback();
    await expect(playPcmStream(broken.getReader(), failed, new AbortController().signal, vi.fn())).rejects.toThrow("cut");
  });
});

describe("read aloud state machine", () => {
  it("creates the audio context before the request and plays one message at a time", async () => {
    const created: FakePlayback[] = [];
    const calls: { voiceId: string; signal: AbortSignal }[] = [];
    let release: (stream: ReadableStream<Uint8Array>) => void = () => undefined;
    const deps = {
      createContext: () => {
        const playback = fakePlayback();
        created.push(playback);
        return playback;
      },
      synthesize: vi.fn((input: { voiceId: string }, signal: AbortSignal) => {
        calls.push({ voiceId: input.voiceId, signal });
        return new Promise<ReadableStream<Uint8Array>>((resolve) => { release = resolve; });
      }),
    };
    const errors: unknown[] = [];
    const onError = (error: unknown) => errors.push(error);
    toggleReadAloud({ messageId: "m1", conversationId: "c1", voiceId: "stella", onError }, deps);
    expect(created).toHaveLength(1);
    expect(created[0]?.resumed).toBe(true);
    expect(getReadAloudSnapshot()).toEqual({ messageId: "m1", status: "loading" });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.voiceId).toBe("stella");
    toggleReadAloud({ messageId: "m2", conversationId: "c1", voiceId: "tide", onError }, deps);
    expect(created[0]?.closed).toBe(true);
    expect(calls[0]?.signal.aborted).toBe(true);
    expect(getReadAloudSnapshot()).toEqual({ messageId: "m2", status: "loading" });
    expect(errors).toEqual([]);

    toggleReadAloud({ messageId: "m2", conversationId: "c1", voiceId: "tide", onError }, deps);
    expect(getReadAloudSnapshot()).toEqual({ messageId: null, status: "idle" });
    expect(errors).toEqual([]);

    toggleReadAloud({ messageId: "m3", conversationId: "c1", voiceId: "stella", onError }, deps);
    await act(async () => { await Promise.resolve(); });
    release(streamOf());
    await act(async () => { await Promise.resolve(); });
    expect((errors.at(-1) as Error).message).toBe("朗读失败，请稍后再试");
    expect(getReadAloudSnapshot().status).toBe("idle");

    let pulled = false;
    const cut = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (!pulled) {
          pulled = true;
          controller.enqueue(new Uint8Array([0x00, 0x00]));
          return;
        }
        controller.error(new Error("upstream"));
      },
    });
    release = () => undefined;
    toggleReadAloud({ messageId: "m4", conversationId: "c1", voiceId: "stella", onError }, deps);
    await act(async () => { await Promise.resolve(); });
    release(cut);
    await act(async () => { await Promise.resolve(); });
    expect((errors.at(-1) as Error).message).toBe("朗读失败，请稍后再试");
    expect(created.at(-1)?.closed).toBe(true);
  });
});

describe("read aloud controls", () => {
  it("changes the voice for the next playback without stopping the current one", async () => {
    class Context {
      sampleRate: number;
      currentTime = 0;
      destination = {};
      constructor(options?: { sampleRate?: number }) { this.sampleRate = options?.sampleRate ?? 0; }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
      createBuffer() { return { duration: 0, copyToChannel() { /* unused */ } }; }
      createBufferSource() { return { buffer: null, connect() { /* unused */ }, start() { /* unused */ }, onended: null }; }
    }
    vi.stubGlobal("AudioContext", Context);
    render(<ReadAloudControls message={message()} conversationId="c1" userId="u9" />);
    fireEvent.click(screen.getByRole("button", { name: "朗读" }));
    await act(async () => { await Promise.resolve(); });
    expect(speechCalls.voices).toEqual(["stella"]);
    expect(screen.getByRole("button", { name: "停止朗读" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "音色 海星（女声）" }));
    fireEvent.click(screen.getByRole("option", { name: "暗潮（男声）" }));
    expect(speechCalls.signals[0]?.aborted).toBe(false);
    expect(readTtsVoice("u9")).toBe("tide");
    fireEvent.click(screen.getByRole("button", { name: "停止朗读" }));
    expect(speechCalls.signals[0]?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "朗读" }));
    await act(async () => { await Promise.resolve(); });
    expect(speechCalls.voices[1]).toBe("tide");
  });
});
