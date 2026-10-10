import { describe, expect, it } from 'vitest';
import { CALL_RUNTIME_KIND, isCallDown, isCallUp, micErrorFor } from '../../src/lib/call-protocol';

describe('call runtime messages', () => {
  it('accepts well-formed messages for the right recipient only', () => {
    const offer = { kind: CALL_RUNTIME_KIND, to: 'worker', msg: { ev: 'offer', sdp: 'v=0' } };
    expect(isCallUp(offer)).toBe(true);
    expect(isCallDown(offer)).toBe(false);
    expect(isCallUp({ ...offer, msg: { ev: 'offer' } })).toBe(false);
    expect(isCallUp({ kind: 'sidecall-picker', to: 'worker', msg: offer.msg })).toBe(false);
    expect(isCallUp({ kind: CALL_RUNTIME_KIND, to: 'worker', msg: { ev: 'error', code: 'mic_permission', message: 'x' } })).toBe(true);
    expect(isCallDown({ kind: CALL_RUNTIME_KIND, to: 'offscreen', msg: { op: 'mute', muted: true } })).toBe(true);
    expect(isCallDown({ kind: CALL_RUNTIME_KIND, to: 'offscreen', msg: { op: 'mute', muted: 'yes' } })).toBe(false);
    expect(isCallDown(null)).toBe(false);
  });

  it('maps getUserMedia errors', () => {
    expect(micErrorFor(new DOMException('Permission dismissed', 'NotAllowedError')).code).toBe('mic_permission');
    expect(micErrorFor(new DOMException('none', 'NotFoundError')).code).toBe('mic_unavailable');
    expect(micErrorFor('weird').code).toBe('mic_unavailable');
  });
});
