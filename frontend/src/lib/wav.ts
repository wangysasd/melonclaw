/** 把采集到的 PCM 样本编码成 ASR 接受的单声道 16-bit PCM WAV。 */

export const SAMPLE_RATE = 16000;
export const MAX_SECONDS = 120;

export function encodeWavBytes(chunks: Float32Array[], length: number): Uint8Array {
  const samples = Math.min(length, MAX_SECONDS * SAMPLE_RATE);
  const buffer = new ArrayBuffer(44 + samples * 2);
  const view = new DataView(buffer);
  const writeString = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, samples * 2, true);
  let offset = 44;
  let written = 0;
  for (const chunk of chunks) {
    for (let index = 0; index < chunk.length && written < samples; index += 1, written += 1) {
      const sample = Math.max(-1, Math.min(1, chunk[index]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new Uint8Array(buffer);
}

export function encodeWav(chunks: Float32Array[], length: number): Blob {
  return new Blob([encodeWavBytes(chunks, length)], { type: "audio/wav" });
}
