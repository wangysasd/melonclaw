import { describe, expect, it } from "vitest";

import { encodeWavBytes, MAX_SECONDS, SAMPLE_RATE } from "../src/lib/wav";

describe("encodeWav", () => {
  it("writes a mono 16-bit PCM header at 16 kHz", () => {
    const samples = new Float32Array([0, 0.5, -1]);
    const bytes = encodeWavBytes([samples], samples.length);
    const view = new DataView(bytes.buffer);
    expect(String.fromCharCode(...bytes.slice(0, 4))).toBe("RIFF");
    expect(String.fromCharCode(...bytes.slice(8, 12))).toBe("WAVE");
    expect(view.getUint16(20, true)).toBe(1);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(SAMPLE_RATE);
    expect(view.getUint16(34, true)).toBe(16);
    expect(view.getUint32(40, true)).toBe(samples.length * 2);
    expect(view.getInt16(44, true)).toBe(0);
    expect(view.getInt16(46, true)).toBe(16383);
    expect(view.getInt16(48, true)).toBe(-32768);
  });

  it("caps the WAV at 120 seconds and excludes samples after that point", () => {
    const limit = MAX_SECONDS * SAMPLE_RATE;
    const first = new Float32Array(limit - 1);
    const last = new Float32Array([1, -1]);
    const bytes = encodeWavBytes([first, last], first.length + last.length);
    const view = new DataView(bytes.buffer);
    expect(bytes.length).toBe(44 + limit * 2);
    expect(view.getUint32(4, true)).toBe(bytes.length - 8);
    expect(view.getUint32(40, true)).toBe(limit * 2);
    expect(view.getInt16(bytes.length - 2, true)).toBe(32767);
  });
});
