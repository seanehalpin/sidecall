// Offscreen call document (SPEC §5.5). The worker creates it for exactly one call
// and closes it on hang-up, so the call starts as soon as the page loads:
//
//   check the mic grant → getUserMedia → RTCPeerConnection + mic track → offer
//   → wait for ICE gathering to finish (non-trickle) → offer to the worker
//   → worker relays webrtc.answer back → remote track plays in <audio>.
//
// chrome.runtime is the only extension API available here.
import { browser } from 'wxt/browser';
import {
  CALL_RUNTIME_KIND,
  ICE_GATHER_TIMEOUT_MS,
  MIC_CONSTRAINTS,
  isCallDown,
  micErrorFor,
  type CallRuntimeUp,
  type CallUp,
} from '@/lib/call-protocol';

const audio = document.getElementById('remote') as HTMLAudioElement;
let pc: RTCPeerConnection | null = null;
let mic: MediaStream | null = null;
let muted = false;
let failed = false;

function up(msg: CallUp): void {
  const wrapped: CallRuntimeUp = { kind: CALL_RUNTIME_KIND, to: 'worker', msg };
  void browser.runtime.sendMessage(wrapped).catch(() => {});
}

function fail(code: Extract<CallUp, { ev: 'error' }>['code'], message: string): void {
  if (failed) return;
  failed = true;
  mic?.getTracks().forEach((t) => t.stop());
  pc?.close();
  up({ ev: 'error', code, message });
}

function applyMute(): void {
  mic?.getAudioTracks().forEach((t) => (t.enabled = !muted));
}

async function micGranted(): Promise<boolean> {
  try {
    const status = await navigator.permissions.query({ name: 'microphone' as PermissionName });
    return status.state === 'granted';
  } catch {
    // Can't tell; let getUserMedia decide.
    return true;
  }
}

function iceComplete(conn: RTCPeerConnection): Promise<void> {
  return new Promise((resolve) => {
    if (conn.iceGatheringState === 'complete') return resolve();
    const timer = setTimeout(resolve, ICE_GATHER_TIMEOUT_MS);
    conn.addEventListener('icegatheringstatechange', () => {
      if (conn.iceGatheringState !== 'complete') return;
      clearTimeout(timer);
      resolve();
    });
  });
}

async function start(): Promise<void> {
  // The offscreen document can't show a permission prompt: without the options-page
  // grant getUserMedia just rejects with NotAllowedError (spike 1).
  if (!(await micGranted())) {
    fail('mic_permission', 'Microphone not granted. Grant it in Sidecall settings, then start the call again.');
    return;
  }
  try {
    mic = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  } catch (err) {
    const { code, message } = micErrorFor(err);
    fail(code, message);
    return;
  }
  applyMute();
  try {
    const conn = new RTCPeerConnection();
    pc = conn;
    conn.ontrack = (e) => {
      audio.srcObject = e.streams[0] ?? new MediaStream([e.track]);
      void audio.play().catch(() => {});
    };
    conn.onconnectionstatechange = () => {
      up({ ev: 'connection', state: conn.connectionState });
      if (conn.connectionState === 'failed') fail('webrtc_failed', 'The audio connection to the daemon failed.');
    };
    // SmallWebRTC sends agent audio back on the same (sendrecv) transceiver.
    for (const track of mic.getTracks()) conn.addTrack(track, mic);
    await conn.setLocalDescription(await conn.createOffer());
    await iceComplete(conn);
    const sdp = conn.localDescription?.sdp;
    if (!sdp) throw new Error('no local description');
    if (!failed) up({ ev: 'offer', sdp });
  } catch (err) {
    fail('webrtc_failed', `Could not set up the audio connection: ${err instanceof Error ? err.message : String(err)}`);
  }
}

browser.runtime.onMessage.addListener((raw: unknown) => {
  if (!isCallDown(raw)) return;
  const msg = raw.msg;
  if (msg.op === 'mute') {
    muted = msg.muted;
    applyMute();
    return;
  }
  if (!pc || failed) return;
  pc.setRemoteDescription({ type: 'answer', sdp: msg.sdp }).then(() => up({ ev: 'answered' })).catch((err: unknown) =>
    fail('webrtc_failed', `The daemon's answer was rejected: ${err instanceof Error ? err.message : String(err)}`),
  );
});

void start();
