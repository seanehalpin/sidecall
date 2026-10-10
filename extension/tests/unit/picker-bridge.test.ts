import { describe, expect, it, vi } from 'vitest';
import { Bridge } from '../../src/lib/picker/bridge';
import { PICKER_CHANNEL, PICKER_RUNTIME_KIND } from '../../src/lib/picker-protocol';

function setup() {
  const win = { postMessage: vi.fn(), location: { origin: 'http://localhost:1' } } as unknown as Window;
  const sendUp = vi.fn(() => Promise.resolve());
  const bridge = new Bridge({ win, sendUp });
  return { win, sendUp, bridge };
}
const handshake = (nonce: string) => ({ channel: PICKER_CHANNEL, dir: 'handshake', nonce });
const up = (nonce: string, msg: unknown) => ({ channel: PICKER_CHANNEL, dir: 'page-to-ext', nonce, msg });

describe('Bridge', () => {
  it('relays page→worker messages that carry the adopted nonce', () => {
    const { win, sendUp, bridge } = setup();
    bridge.onWindowMessage({ source: win, data: handshake('n1') });
    bridge.onWindowMessage({ source: win, data: up('n1', { t: 'escape' }) });
    expect(sendUp).toHaveBeenCalledWith({ kind: PICKER_RUNTIME_KIND, msg: { t: 'escape' } });
  });

  it('the first handshake wins; later nonces are ignored', () => {
    const { win, sendUp, bridge } = setup();
    bridge.onWindowMessage({ source: win, data: handshake('n1') });
    bridge.onWindowMessage({ source: win, data: handshake('evil') });
    bridge.onWindowMessage({ source: win, data: up('evil', { t: 'escape' }) });
    expect(sendUp).not.toHaveBeenCalled();
  });

  it('ignores messages from other windows and with the wrong shape', () => {
    const { win, sendUp, bridge } = setup();
    bridge.onWindowMessage({ source: win, data: handshake('n1') });
    bridge.onWindowMessage({ source: {}, data: up('n1', { t: 'escape' }) });
    bridge.onWindowMessage({ source: win, data: { channel: 'other', dir: 'page-to-ext', nonce: 'n1', msg: {} } });
    bridge.onWindowMessage({ source: win, data: null });
    expect(sendUp).not.toHaveBeenCalled();
  });

  it('adopts the nonce of the first page message if the handshake was missed', () => {
    const { win, sendUp, bridge } = setup();
    bridge.onWindowMessage({ source: win, data: up('late', { t: 'ready', url: 'u' }) });
    expect(sendUp).toHaveBeenCalledTimes(1);
  });

  it('relays worker→page as a nonce envelope, and only once it has a nonce', () => {
    const { win, bridge } = setup();
    const m = { kind: PICKER_RUNTIME_KIND, msg: { t: 'pins-clear' } };
    bridge.onRuntimeMessage(m);
    expect(win.postMessage).not.toHaveBeenCalled();
    bridge.onWindowMessage({ source: win, data: handshake('n1') });
    bridge.onRuntimeMessage(m);
    bridge.onRuntimeMessage({ kind: 'other', msg: {} });
    expect(win.postMessage).toHaveBeenCalledTimes(1);
    expect(win.postMessage).toHaveBeenCalledWith(
      { channel: PICKER_CHANNEL, nonce: 'n1', dir: 'ext-to-page', msg: { t: 'pins-clear' } },
      '*',
    );
  });

  it('swallows send failures (extension reloaded)', async () => {
    const win = { postMessage: vi.fn() } as unknown as Window;
    const bridge = new Bridge({ win, sendUp: () => Promise.reject(new Error('Extension context invalidated.')) });
    bridge.onWindowMessage({ source: win, data: handshake('n') });
    expect(() => bridge.onWindowMessage({ source: win, data: up('n', { t: 'escape' }) })).not.toThrow();
    const throwing = new Bridge({ win, sendUp: () => { throw new Error('x'); } });
    throwing.onWindowMessage({ source: win, data: handshake('n') });
    expect(() => throwing.onWindowMessage({ source: win, data: up('n', { t: 'escape' }) })).not.toThrow();
    await Promise.resolve();
  });
});
