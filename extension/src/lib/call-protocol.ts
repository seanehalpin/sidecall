// Messages between the service worker and the offscreen call document (SPEC §5.5).
// Both directions go over chrome.runtime.sendMessage, tagged so the picker's
// runtime messages and these never mix.

export const OFFSCREEN_PATH = '/offscreen.html';
export const CALL_RUNTIME_KIND = 'sidecall-call';

/** Worker → offscreen document. */
export type CallDown =
  | { op: 'answer'; sdp: string }
  | { op: 'mute'; muted: boolean };

/** Error codes the offscreen document reports. */
export type CallErrorCode = 'mic_permission' | 'mic_unavailable' | 'webrtc_failed';

/** Offscreen document → worker. */
export type CallUp =
  | { ev: 'offer'; sdp: string }
  /** The daemon's answer was applied (signalling done; ICE/DTLS may still be in progress). */
  | { ev: 'answered' }
  | { ev: 'connection'; state: RTCPeerConnectionState }
  | { ev: 'error'; code: CallErrorCode; message: string };

export interface CallRuntimeDown {
  kind: typeof CALL_RUNTIME_KIND;
  to: 'offscreen';
  msg: CallDown;
}

export interface CallRuntimeUp {
  kind: typeof CALL_RUNTIME_KIND;
  to: 'worker';
  msg: CallUp;
}

function tagged(value: unknown, to: 'offscreen' | 'worker'): value is { msg: Record<string, unknown> } {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return v.kind === CALL_RUNTIME_KIND && v.to === to && !!v.msg && typeof v.msg === 'object';
}

export function isCallUp(value: unknown): value is CallRuntimeUp {
  if (!tagged(value, 'worker')) return false;
  const m = value.msg;
  switch (m.ev) {
    case 'offer':
      return typeof m.sdp === 'string';
    case 'answered':
      return true;
    case 'connection':
      return typeof m.state === 'string';
    case 'error':
      return typeof m.code === 'string' && typeof m.message === 'string';
    default:
      return false;
  }
}

export function isCallDown(value: unknown): value is CallRuntimeDown {
  if (!tagged(value, 'offscreen')) return false;
  const m = value.msg;
  return (m.op === 'answer' && typeof m.sdp === 'string') || (m.op === 'mute' && typeof m.muted === 'boolean');
}

export const MIC_CONSTRAINTS: MediaStreamConstraints = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

/** How long to wait for ICE gathering before sending the offer with what we have. */
export const ICE_GATHER_TIMEOUT_MS = 2_000;

/** User-facing text for a failed getUserMedia. */
export function micErrorFor(err: unknown): { code: CallErrorCode; message: string } {
  const name = err instanceof Error || (err && typeof err === 'object' && 'name' in err) ? String((err as { name: unknown }).name) : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return { code: 'mic_permission', message: 'Microphone not granted. Grant it in Sidecall settings, then start the call again.' };
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return { code: 'mic_unavailable', message: 'No microphone found.' };
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return { code: 'mic_unavailable', message: 'The microphone is in use or unavailable.' };
  }
  return { code: 'mic_unavailable', message: `Could not open the microphone${err instanceof Error ? `: ${err.message}` : '.'}` };
}
