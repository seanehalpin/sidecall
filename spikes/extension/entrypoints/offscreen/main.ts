// Offscreen document (reasons USER_MEDIA + WEB_RTC): holds the mic, records,
// plays audio. Only chrome.runtime is available here.
import { db, decimate, encodeWav, peak, rms, xcorrPeak } from '@/lib/dsp';
import { putBlob } from '@/lib/store';
import {
  CONSTRAINTS,
  type Aec,
  type EchoCapture,
  type EchoResult,
  type MicError,
  type OffscreenRequest,
  type Record5Result,
  type Variant,
} from '@/lib/messages';

const SR = 48000;
let ctx: AudioContext | null = null;

async function audioCtx(): Promise<AudioContext> {
  if (!ctx) {
    ctx = new AudioContext({ sampleRate: SR });
    await ctx.audioWorklet.addModule(browser.runtime.getURL('/recorder-worklet.js'));
  }
  if (ctx.state !== 'running') await ctx.resume();
  return ctx;
}

function progress(text: string) {
  console.log('[offscreen]', text);
  browser.runtime.sendMessage({ target: 'panel', type: 'progress', text }).catch(() => {});
}

async function permissionState(): Promise<string> {
  try {
    return (await navigator.permissions.query({ name: 'microphone' as PermissionName })).state;
  } catch (e) {
    return `query-failed: ${String(e)}`;
  }
}

/** Starts capturing the mic into memory. Call stop() to get samples. */
async function startRecorder(stream: MediaStream) {
  const c = await audioCtx();
  const src = c.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(c, 'recorder');
  const mute = c.createGain();
  mute.gain.value = 0;
  src.connect(node).connect(mute).connect(c.destination);
  const chunks: Float32Array[] = [];
  let count = 0;
  node.port.onmessage = (e: MessageEvent<Float32Array>) => {
    chunks.push(e.data);
    count += e.data.length;
  };
  return {
    get length() {
      return count;
    },
    stop(): Float32Array<ArrayBuffer> {
      node.port.onmessage = null;
      src.disconnect();
      node.disconnect();
      mute.disconnect();
      const out = new Float32Array(count);
      let o = 0;
      for (const ch of chunks) {
        out.set(ch, o);
        o += ch.length;
      }
      return out;
    },
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function toMicError(e: unknown, context: string, permission?: string): MicError {
  const err = e as DOMException;
  return { ok: false, name: err?.name ?? 'Error', message: err?.message ?? String(e), context, permission };
}

async function record5(): Promise<Record5Result | MicError> {
  const permission = await permissionState();
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: CONSTRAINTS.on });
  } catch (e) {
    return toMicError(e, 'offscreen getUserMedia', permission);
  }
  const settings = stream.getAudioTracks()[0].getSettings();
  progress('Recording 5 s…');
  const rec = await startRecorder(stream);
  await sleep(5000);
  const samples = rec.stop();
  stream.getTracks().forEach((t) => t.stop());
  const key = 'record5';
  await putBlob(key, encodeWav(samples, SR));
  progress('Playing back from offscreen document…');
  const c = await audioCtx();
  const buf = c.createBuffer(1, samples.length, SR);
  buf.copyToChannel(samples, 0);
  const node = c.createBufferSource();
  node.buffer = buf;
  node.connect(c.destination);
  await new Promise<void>((resolve) => {
    node.onended = () => resolve();
    node.start();
  });
  return {
    ok: true,
    durationS: samples.length / SR,
    sampleRate: SR,
    rmsDb: db(rms(samples)),
    peak: peak(samples),
    permission,
    settings,
    key,
  };
}

// ---------- Echo test ----------

let refBuffer: AudioBuffer | null = null;
async function reference(): Promise<AudioBuffer> {
  if (!refBuffer) {
    const c = await audioCtx();
    const res = await fetch(browser.runtime.getURL('/speech.wav'));
    refBuffer = await c.decodeAudioData(await res.arrayBuffer());
  }
  return refBuffer;
}

/** Plays the reference clip through the given path; resolves when playback is done. */
async function play(variant: Variant): Promise<void> {
  const c = await audioCtx();
  const ref = await reference();
  if (variant === 'element') {
    const a = new Audio(browser.runtime.getURL('/speech.wav'));
    await a.play();
    await new Promise((r) => (a.onended = r));
    return;
  }
  if (variant === 'webaudio') {
    const s = c.createBufferSource();
    s.buffer = ref;
    s.connect(c.destination);
    await new Promise<void>((r) => {
      s.onended = () => r();
      s.start();
    });
    return;
  }
  // webrtc: local RTCPeerConnection loopback. The clip is sent as a WebRTC
  // track (Opus) and the *remote* track is played via an <audio> element —
  // the same path agent audio from Pipecat SmallWebRTC will take.
  const pc1 = new RTCPeerConnection();
  const pc2 = new RTCPeerConnection();
  pc1.onicecandidate = (e) => e.candidate && pc2.addIceCandidate(e.candidate);
  pc2.onicecandidate = (e) => e.candidate && pc1.addIceCandidate(e.candidate);
  const dest = c.createMediaStreamDestination();
  pc1.addTrack(dest.stream.getAudioTracks()[0], dest.stream);
  const remote = new Promise<MediaStream>((r) => {
    pc2.ontrack = (e) => r(e.streams[0] ?? new MediaStream([e.track]));
  });
  const offer = await pc1.createOffer();
  await pc1.setLocalDescription(offer);
  await pc2.setRemoteDescription(offer);
  const answer = await pc2.createAnswer();
  await pc2.setLocalDescription(answer);
  await pc1.setRemoteDescription(answer);
  const stream = await remote;
  const a = new Audio();
  a.srcObject = stream;
  await a.play();
  // wait for ICE to connect before starting the clip
  for (let i = 0; i < 50 && pc1.connectionState !== 'connected'; i++) await sleep(100);
  const s = c.createBufferSource();
  s.buffer = ref;
  s.connect(dest);
  await new Promise<void>((r) => {
    s.onended = () => r();
    s.start();
  });
  await sleep(300); // jitter buffer drain
  a.pause();
  a.srcObject = null;
  pc1.close();
  pc2.close();
}

async function capture(variant: Variant, aec: Aec, refDec: Float32Array): Promise<EchoCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: CONSTRAINTS[aec] });
  const settings = stream.getAudioTracks()[0].getSettings();
  const rec = await startRecorder(stream);
  await sleep(1500); // pre-roll: noise floor + let AEC settle
  const playStart = rec.length;
  await play(variant);
  await sleep(500);
  const samples = rec.stop();
  stream.getTracks().forEach((t) => t.stop());
  const key = `echo-${variant}-${aec}`;
  await putBlob(key, encodeWav(samples, SR));
  const prerollDb = db(rms(samples, Math.floor(SR * 0.3), playStart));
  const playDb = db(rms(samples, playStart, samples.length));
  const capDec = decimate(samples.subarray(playStart), 3);
  const { coef, lag } = xcorrPeak(capDec, refDec, Math.floor(16000 * 0.5));
  return {
    variant,
    aec,
    key,
    prerollDb,
    playDb,
    leakDb: playDb - prerollDb,
    xcorr: coef,
    lagMs: (lag / 16000) * 1000,
    settings,
  };
}

async function echoTest(variants: Variant[]): Promise<EchoResult | MicError> {
  const ref = await reference();
  const refData = ref.getChannelData(0);
  const refDec = decimate(refData, Math.round(ref.sampleRate / 16000));
  await putBlob('reference', encodeWav(refData, ref.sampleRate));
  let capabilities: unknown = null;
  const captures: EchoCapture[] = [];
  try {
    const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
    capabilities = probe.getAudioTracks()[0].getCapabilities?.() ?? null;
    probe.getTracks().forEach((t) => t.stop());
    const ecCaps = ((capabilities as { echoCancellation?: unknown[] } | null)?.echoCancellation ?? []) as unknown[];
    const modes: Aec[] = ['on', ...(['all', 'remote-only'] as Aec[]).filter((m) => ecCaps.includes(m)), 'off'];
    for (const v of variants) {
      for (const aec of modes) {
        progress(`Echo test: ${v} playback, AEC ${aec}…`);
        captures.push(await capture(v, aec, refDec));
        await sleep(500);
      }
    }
  } catch (e) {
    return toMicError(e, 'echo test', await permissionState());
  }
  progress('Echo test done.');
  return { ok: true, refDb: db(rms(refData)), captures, capabilities, userAgent: navigator.userAgent };
}

browser.runtime.onMessage.addListener((msg: OffscreenRequest, _sender, sendResponse) => {
  if (msg?.target !== 'offscreen') return false;
  const run = async () => {
    switch (msg.type) {
      case 'ping':
        return { ok: true, permission: await permissionState() };
      case 'record5':
        return record5();
      case 'echo-test':
        return echoTest(msg.variants);
    }
  };
  run()
    .then(sendResponse)
    .catch((e) => sendResponse(toMicError(e, `offscreen ${msg.type}`)));
  return true;
});
