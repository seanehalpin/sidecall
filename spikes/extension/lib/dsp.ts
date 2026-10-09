// Pure DSP helpers for the spikes: RMS, dB, decimation, normalised
// cross-correlation (FFT), WAV encoding.

export function rms(x: Float32Array, start = 0, end = x.length): number {
  let s = 0;
  const n = Math.max(1, end - start);
  for (let i = start; i < end; i++) s += x[i] * x[i];
  return Math.sqrt(s / n);
}

export function db(v: number): number {
  return 20 * Math.log10(Math.max(v, 1e-9));
}

export function peak(x: Float32Array): number {
  let p = 0;
  for (let i = 0; i < x.length; i++) p = Math.max(p, Math.abs(x[i]));
  return p;
}

/** Integer-factor decimation with a boxcar pre-filter (good enough for speech correlation). */
export function decimate(x: Float32Array, factor: number): Float32Array {
  const out = new Float32Array(Math.floor(x.length / factor));
  for (let i = 0; i < out.length; i++) {
    let s = 0;
    for (let k = 0; k < factor; k++) s += x[i * factor + k];
    out[i] = s / factor;
  }
  return out;
}

function fft(re: Float64Array, im: Float64Array, inverse: boolean): void {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = ((inverse ? 2 : -2) * Math.PI) / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let k = 0; k < len / 2; k++) {
        const ar = re[i + k], ai = im[i + k];
        const br = re[i + k + len / 2] * cr - im[i + k + len / 2] * ci;
        const bi = re[i + k + len / 2] * ci + im[i + k + len / 2] * cr;
        re[i + k] = ar + br; im[i + k] = ai + bi;
        re[i + k + len / 2] = ar - br; im[i + k + len / 2] = ai - bi;
        const t = cr * wr - ci * wi;
        ci = cr * wi + ci * wr; cr = t;
      }
    }
  }
  if (inverse) for (let i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
}

/**
 * Max normalised cross-correlation of `cap` against `ref` for lags in
 * [0, maxLag] (cap delayed relative to ref). Returns coefficient in [0,1]
 * (absolute value) and the lag in samples. Normalised per lag by the energy
 * of the overlapping capture window, so it is level-independent.
 */
export function xcorrPeak(cap: Float32Array, ref: Float32Array, maxLag: number): { coef: number; lag: number } {
  let n = 1;
  while (n < cap.length + ref.length) n <<= 1;
  const ar = new Float64Array(n), ai = new Float64Array(n);
  const br = new Float64Array(n), bi = new Float64Array(n);
  ar.set(cap);
  br.set(ref);
  fft(ar, ai, false);
  fft(br, bi, false);
  // cap * conj(ref)
  for (let i = 0; i < n; i++) {
    const r = ar[i] * br[i] + ai[i] * bi[i];
    const im = ai[i] * br[i] - ar[i] * bi[i];
    ar[i] = r; ai[i] = im;
  }
  fft(ar, ai, true);
  const prefix = new Float64Array(cap.length + 1);
  for (let i = 0; i < cap.length; i++) prefix[i + 1] = prefix[i] + cap[i] * cap[i];
  let refE = 0;
  for (let i = 0; i < ref.length; i++) refE += ref[i] * ref[i];
  let best = 0, bestLag = 0;
  for (let lag = 0; lag <= maxLag && lag < cap.length; lag++) {
    const end = Math.min(cap.length, lag + ref.length);
    const capE = prefix[end] - prefix[lag];
    if (capE <= 0 || refE <= 0) continue;
    const c = Math.abs(ar[lag]) / Math.sqrt(capE * refE);
    if (c > best) { best = c; bestLag = lag; }
  }
  return { coef: best, lag: bestLag };
}

export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE');
  w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}
