import { useEffect, useRef, useState } from "react";

const BAR_WIDTH = 2;
const BAR_GAP = 3;
const BAR_MIN = 5;
const BAR_MAX = 36;
const MAX_BARS = 60;
const FREQ_LOW = 100;
const FREQ_HIGH = 4000;
const DB_FLOOR = -80;
const DB_CEIL = -25;
const TILT_DB_PER_OCTAVE = 4;

interface Range {
  from: number;
  to: number;
  center: number;
}

function buildRanges(count: number, binWidth: number, binCount: number): Range[] {
  const ranges: Range[] = [];
  const ratio = FREQ_HIGH / FREQ_LOW;
  for (let index = 0; index < count; index += 1) {
    const low = FREQ_LOW * ratio ** (index / count);
    const high = FREQ_LOW * ratio ** ((index + 1) / count);
    const from = Math.min(binCount - 1, Math.floor(low / binWidth));
    const to = Math.min(binCount, Math.max(from + 1, Math.ceil(high / binWidth)));
    ranges.push({ from, to, center: Math.sqrt(low * high) / binWidth });
  }
  return ranges;
}

/** 窄屏录音时盖住输入区。每帧只改 transform，减少动效时保持静止。 */
export function VoiceSpectrum({ getAnalyser }: { getAnalyser: () => AnalyserNode | null }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [count, setCount] = useState(0);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const apply = (width: number) => {
      const fit = Math.floor((width + BAR_GAP) / (BAR_WIDTH + BAR_GAP));
      setCount(Math.min(MAX_BARS, Math.max(0, fit)));
    };
    apply(root.clientWidth);
    if (typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(([entry]) => apply(entry.contentRect.width));
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || count < 1) return;
    const reduced = typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;
    const bars = Array.from(root.children) as HTMLElement[];
    const levels = new Float32Array(count);
    const targets = new Float32Array(count);
    const tilts = new Float32Array(count);
    for (let index = 0; index < count; index += 1) {
      tilts[index] = TILT_DB_PER_OCTAVE * Math.log2(FREQ_HIGH / FREQ_LOW) * (index / count);
    }
    let data = new Float32Array(0);
    let ranges: Range[] = [];
    let rangeKey = "";
    let frame = 0;
    const draw = () => {
      frame = window.requestAnimationFrame(draw);
      const analyser = getAnalyser();
      if (analyser) {
        if (data.length !== analyser.frequencyBinCount) data = new Float32Array(analyser.frequencyBinCount);
        const key = `${analyser.context.sampleRate}:${analyser.fftSize}`;
        if (key !== rangeKey) {
          rangeKey = key;
          ranges = buildRanges(count, analyser.context.sampleRate / analyser.fftSize, data.length);
        }
        analyser.getFloatFrequencyData(data);
      }
      for (let index = 0; index < count; index += 1) {
        let target = 0;
        if (analyser && ranges[index]) {
          const { from, to, center } = ranges[index];
          let peak = Number.NEGATIVE_INFINITY;
          if (to - from <= 1) {
            const base = Math.min(data.length - 2, Math.floor(center));
            const weight = center - base;
            peak = data[base] * (1 - weight) + data[base + 1] * weight;
          } else {
            for (let bin = from; bin < to; bin += 1) peak = Math.max(peak, data[bin]);
          }
          const db = peak + tilts[index];
          target = Math.min(1, Math.max(0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)));
        }
        targets[index] = target;
      }
      for (let index = 0; index < count; index += 1) {
        const previous = targets[Math.max(0, index - 1)];
        const next = targets[Math.min(count - 1, index + 1)];
        const target = (previous + targets[index] * 2 + next) / 4;
        levels[index] += (target - levels[index]) * (target > levels[index] ? 0.8 : 0.35);
        const height = BAR_MIN + (BAR_MAX - BAR_MIN) * levels[index];
        bars[index].style.transform = `scaleY(${height / BAR_MIN})`;
      }
    };
    frame = window.requestAnimationFrame(draw);
    return () => window.cancelAnimationFrame(frame);
  }, [count, getAnalyser]);

  return (
    <div ref={rootRef} className="voice-spectrum" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => <span key={index} className="voice-spectrum-bar" />)}
    </div>
  );
}
