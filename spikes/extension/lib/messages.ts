/**
 * 'on' = echoCancellation: true (production constraint). 'all' / 'remote-only'
 * are the newer enum values of the echoCancellation constraint (Media Capture
 * spec); Chromium 153 lists them in getCapabilities(). Tested only if offered.
 */
export type Aec = 'on' | 'all' | 'remote-only' | 'off';
export type Variant = 'element' | 'webaudio' | 'webrtc';

export const CONSTRAINTS: Record<Aec, MediaTrackConstraints> = {
  // Production constraints (SPEC §5.5).
  on: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  all: { echoCancellation: 'all' as unknown as boolean, noiseSuppression: true, autoGainControl: true },
  'remote-only': { echoCancellation: 'remote-only' as unknown as boolean, noiseSuppression: true, autoGainControl: true },
  // Everything off so the raw leakage is visible and AGC doesn't skew levels.
  off: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
};

export interface MicError {
  ok: false;
  name: string;
  message: string;
  context: string;
  permission?: string;
}

export interface Record5Result {
  ok: true;
  durationS: number;
  sampleRate: number;
  rmsDb: number;
  peak: number;
  permission: string;
  settings: MediaTrackSettings;
  key: string;
}

export interface EchoCapture {
  variant: Variant;
  aec: Aec;
  key: string;
  prerollDb: number; // mic level before playback (noise floor)
  playDb: number; // mic level during playback
  leakDb: number; // playDb - prerollDb
  xcorr: number; // peak normalised cross-correlation vs reference (0..1)
  lagMs: number;
  settings: MediaTrackSettings;
}

export interface EchoResult {
  ok: true;
  refDb: number;
  captures: EchoCapture[];
  capabilities: unknown;
  userAgent: string;
}

export type OffscreenRequest =
  | { target: 'offscreen'; type: 'record5' }
  | { target: 'offscreen'; type: 'echo-test'; variants: Variant[] }
  | { target: 'offscreen'; type: 'ping' };

export interface ProgressMsg {
  target: 'panel';
  type: 'progress';
  text: string;
}
