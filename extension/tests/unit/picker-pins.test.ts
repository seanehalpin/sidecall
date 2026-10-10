// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Anchor, PageToExt, PinSpec } from '../../src/lib/picker-protocol';
import type { AnchorEnv } from '../../src/lib/picker/anchor';
import { PinRegistry, RESTORE_GRACE_MS } from '../../src/lib/picker/pins';
import { textFingerprint } from '../../src/lib/picker/util';

const env: AnchorEnv<Element> = {
  isConnected: (e) => e.isConnected,
  tagOf: (e) => e.tagName.toLowerCase(),
  textOf: (e) => textFingerprint(e.textContent ?? ''),
  sourceOf: async () => null,
  queryAll: (s) => Array.from(document.querySelectorAll(s)),
  byTag: (t) => Array.from(document.getElementsByTagName(t)),
};

const anchor = (over: Partial<Anchor> = {}): Anchor => ({
  tag: 'h2', source: null, selector: 'h2.t', altSelectors: [], textFingerprint: 'hello', repeatIndex: null, region: null, ...over,
});
const spec = (id: number, a: Anchor): PinSpec => ({ id, anchor: a, label: 'x', dimmed: false });

function make() {
  const posted: PageToExt[] = [];
  const overlay = { renderPins: vi.fn() } as never;
  const reg = new PinRegistry({ overlay, post: (m) => posted.push(m), env, refreshAnchor: async (_e, prev) => prev });
  return { reg, posted };
}

describe('PinRegistry', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('requestAnimationFrame', (f: () => void) => setTimeout(f, 0) as unknown as number);
    document.body.innerHTML = '<h2 class="t">Hello</h2>';
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('emits nothing while an element stays put', async () => {
    const { reg, posted } = make();
    const el = document.querySelector('h2')!;
    reg.addLocal(spec(1, anchor()), el, null);
    await reg.reanchorAll();
    await reg.reanchorAll();
    expect(posted).toEqual([]);
  });

  it('emits pin-status once when the element is replaced, then stays quiet', async () => {
    const { reg, posted } = make();
    reg.addLocal(spec(1, anchor()), document.querySelector('h2')!, null);
    document.body.innerHTML = '<h2 class="t">Hello</h2>'; // rebuilt DOM (HMR)
    await reg.reanchorAll();
    await reg.reanchorAll();
    expect(posted).toHaveLength(1);
    expect(posted[0]).toMatchObject({ t: 'pin-status', id: 1, status: 'ok' });
  });

  it('goes stale when the element disappears, and recovers when it returns', async () => {
    const { reg, posted } = make();
    reg.addLocal(spec(1, anchor()), document.querySelector('h2')!, null);
    document.body.innerHTML = '';
    await reg.reanchorAll();
    expect(posted.at(-1)).toMatchObject({ status: 'stale' });
    document.body.innerHTML = '<h2 class="t">Hello</h2>';
    await reg.reanchorAll();
    expect(posted.at(-1)).toMatchObject({ status: 'ok' });
    expect(posted).toHaveLength(2);
  });

  it('restore: waits quietly during the grace period, then reports stale', async () => {
    const { reg, posted } = make();
    document.body.innerHTML = '';
    reg.restore([spec(7, anchor())]);
    await vi.advanceTimersByTimeAsync(50);
    expect(posted).toEqual([]);
    document.body.innerHTML = '<h2 class="t">Hello</h2>';
    reg.scheduleReanchor();
    await vi.advanceTimersByTimeAsync(300);
    expect(posted).toEqual([expect.objectContaining({ t: 'pin-status', id: 7, status: 'ok' })]);
  });

  it('restore: an unmatched pin is reported stale after the grace period', async () => {
    const { reg, posted } = make();
    document.body.innerHTML = '';
    reg.restore([spec(7, anchor())]);
    await vi.advanceTimersByTimeAsync(RESTORE_GRACE_MS + 500);
    expect(posted).toEqual([expect.objectContaining({ id: 7, status: 'stale' })]);
  });

  it('tracks live (current-turn) pins for Esc, and dimming', () => {
    const { reg } = make();
    reg.addLocal(spec(1, anchor()), document.querySelector('h2')!, null);
    expect(reg.hasLive).toBe(true);
    reg.dim([1], true);
    expect(reg.hasLive).toBe(false);
    reg.clear();
    expect(reg.size).toBe(0);
  });
});
