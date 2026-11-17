/**
 * 录音并编码为 16kHz 单声道 16-bit PCM WAV。
 * 浏览器负责重采样；页面内完成编码，避免 MediaRecorder 产出接口不接受的容器。
 */

import { encodeWav, MAX_SECONDS, SAMPLE_RATE } from "./wav";

const BUFFER_SIZE = 4096;

export interface Recording {
  stop: () => Promise<Blob | null>;
  cancel: () => void;
  analyser: AnalyserNode;
}

/** 开始录音。到达 120 秒时调用 onLimit，由调用方结束并转写。 */
export async function startRecording(onLimit: () => void): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  let context: AudioContext | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let processor: ScriptProcessorNode | undefined;
  let analyser: AnalyserNode | undefined;
  let released = false;

  const release = () => {
    if (released) return;
    released = true;
    if (processor) processor.onaudioprocess = null;
    for (const node of [processor, analyser, source]) {
      try {
        node?.disconnect();
      } catch {
        // 单个节点断开失败时继续释放麦克风。
      }
    }
    for (const track of stream.getTracks()) track.stop();
    void context?.close().catch(() => undefined);
  };

  try {
    context = new AudioContext({ sampleRate: SAMPLE_RATE });
    source = context.createMediaStreamSource(stream);
    processor = context.createScriptProcessor(BUFFER_SIZE, 1, 1);
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.2;
    const chunks: Float32Array[] = [];
    let length = 0;
    let limited = false;
    processor.onaudioprocess = (event) => {
      if (limited) return;
      chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      length += BUFFER_SIZE;
      if (length >= MAX_SECONDS * SAMPLE_RATE) {
        limited = true;
        onLimit();
      }
    };
    source.connect(analyser);
    analyser.connect(processor);
    processor.connect(context.destination);
    if (context.state === "suspended") void context.resume().catch(() => undefined);
    return {
      stop: async () => {
        release();
        return length === 0 ? null : encodeWav(chunks, length);
      },
      cancel: release,
      analyser,
    };
  } catch (error) {
    release();
    throw error;
  }
}
