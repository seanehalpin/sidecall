import { describe, expect, it } from 'vitest';
import { db, decimate, rms, xcorrPeak } from '../lib/dsp';

// Deterministic pseudo-speech: noise bursts with an amplitude envelope.
function noise(n: number, seed = 1): Float32Array {
  // mulberry32 PRNG: independent streams per seed (unlike a naive LCG)
  let a = seed >>> 0;
  const rnd = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = (rnd() * 2 - 1) * (0.5 + 0.5 * Math.sin(i / 800 + seed));
  return out;
}

describe('dsp', () => {
  it('rms/db of a full-scale sine is about -3 dBFS', () => {
    const x = new Float32Array(48000).map((_, i) => Math.sin((2 * Math.PI * 440 * i) / 48000));
    expect(db(rms(x))).toBeCloseTo(-3.01, 1);
  });

  it('finds the lag of a delayed, attenuated, noisy echo', () => {
    const ref = noise(16000 * 3);
    const lag = 800; // 50 ms at 16 kHz
    const cap = new Float32Array(ref.length + 4000);
    const n = noise(cap.length, 99);
    for (let i = 0; i < cap.length; i++) cap[i] = 0.05 * n[i] + (i >= lag && i - lag < ref.length ? 0.3 * ref[i - lag] : 0);
    const r = xcorrPeak(cap, ref, 8000);
    expect(r.lag).toBe(lag);
    expect(r.coef).toBeGreaterThan(0.9);
  });

  it('reports low correlation for unrelated signals', () => {
    const r = xcorrPeak(noise(48000, 7), noise(40000, 3), 8000);
    expect(r.coef).toBeLessThan(0.1);
  });

  it('decimates by an integer factor', () => {
    expect(decimate(new Float32Array([1, 1, 1, 2, 2, 2]), 3)).toEqual(new Float32Array([1, 2]));
  });
});
