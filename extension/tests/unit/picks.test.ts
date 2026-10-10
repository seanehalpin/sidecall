import { describe, expect, it, vi } from 'vitest';
import type { PickDraft } from '../../src/lib/picker-protocol';
import { AckWaiter } from '../../src/lib/picks/ack';
import { buildChip, buildPin, chipTitle } from '../../src/lib/picks/chip';
import { createCaptureGate } from '../../src/lib/picks/gate';
import { clipRect, planCrop, planViewport } from '../../src/lib/picks/geometry';
import { insertAtRange } from '../../src/lib/picks/insert';
import { parseMarkers, segmentMarkers } from '../../src/lib/picks/markers';
import { daemonTime, nextPickId, selectPickIds } from '../../src/lib/picks/turn';

const draft = (patch: Partial<PickDraft> = {}): PickDraft => ({
  kind: 'element',
  page: { url: 'http://localhost:5173/', route: '/', title: '', viewport: { w: 1280, h: 800, dpr: 2 }, scroll: { x: 0, y: 0 }, colorScheme: 'light', consoleErrors: [] },
  element: { selector: 'div.card-header', altSelectors: [], tag: 'div', attributes: { class: 'card-header big' }, text: '', outerHTML: '', rect: { x: 0, y: 0, w: 1, h: 1 }, computedStyles: {} },
  region: null,
  framework: {
    name: 'svelte',
    version: null,
    source: { file: 'src/lib/Card.svelte', line: 14, column: 3, precision: 'element' },
    componentChain: [{ name: 'CardHeader', type: null, file: 'src/lib/Card.svelte', line: 14, column: 3 }],
    props: {},
  },
  repeat: { count: 12, index: 2 },
  ...patch,
});

describe('markers', () => {
  it('parses distinct [n] in order', () => {
    expect(parseMarkers('make [2] match [1], and [2] again [x] [0] []')).toEqual([2, 1]);
    expect(parseMarkers('nothing')).toEqual([]);
  });
  it('segments text', () => {
    expect(segmentMarkers('a [1] b')).toEqual([
      { kind: 'text', text: 'a ' },
      { kind: 'marker', id: 1, raw: '[1]' },
      { kind: 'text', text: ' b' },
    ]);
    expect(segmentMarkers('[3]')).toEqual([{ kind: 'marker', id: 3, raw: '[3]' }]);
  });
});

describe('selectPickIds', () => {
  const live = [
    { id: 1, dimmed: true },
    { id: 2, dimmed: false },
    { id: 4, dimmed: false },
  ];
  it('takes non-dimmed picks plus existing referenced ones', () => {
    expect(selectPickIds(live, 'make [1] look like it')).toEqual([1, 2, 4]);
    expect(selectPickIds(live, 'hello')).toEqual([2, 4]);
    expect(selectPickIds(live, 'see [9]')).toEqual([2, 4]);
    expect(selectPickIds([{ id: 1, dimmed: true }, { id: 2, dimmed: true }], 'make [2] match [1]')).toEqual([1, 2]);
    expect(selectPickIds([], 'make [1]')).toEqual([]);
  });
});

describe('ids and clock', () => {
  it('never reuses ids', () => {
    expect(nextPickId(0, [])).toBe(1);
    expect(nextPickId(5, [2])).toBe(6);
    expect(nextPickId(0, [3, 7])).toBe(8);
  });
  it('converts to daemon time', () => {
    expect(daemonTime(1000, 250)).toBe(1250);
    expect(daemonTime(1000, -300)).toBe(700);
    expect(daemonTime(1000, null)).toBe(1000);
  });
});

describe('geometry with a bitmap shorter than the viewport', () => {
  // A headless window can report a 1280x720 viewport but capture only 1280x633.
  const viewport = { w: 1280, h: 720 };
  const bitmap = { w: 1280, h: 633 };
  const rect = { x: 24, y: 70, w: 200, h: 45 };

  it('does not squash the outline vertically', () => {
    const plan = planViewport(rect, viewport, bitmap);
    expect(plan.outline).toEqual(rect);
  });

  it('crops at the true pixel position', () => {
    const plan = planCrop(rect, viewport, bitmap, 0);
    expect(plan.sy).toBe(70);
    expect(plan.sh).toBe(45);
  });
});

describe('geometry', () => {
  const vp = { w: 1280, h: 800 };
  it('clips with margin', () => {
    expect(clipRect({ x: 20, y: 20, w: 200, h: 50 }, vp, 16)).toEqual({ x: 4, y: 4, w: 232, h: 82 });
    expect(clipRect({ x: 1200, y: 780, w: 200, h: 100 }, vp, 16)).toEqual({ x: 1184, y: 764, w: 96, h: 36 });
    expect(clipRect({ x: 2000, y: 0, w: 10, h: 10 }, vp, 16)).toBeNull();
  });
  it('plans a native-resolution crop at 2x', () => {
    const p = planCrop({ x: 20, y: 20, w: 200, h: 50 }, vp, { w: 2560, h: 1600 });
    expect(p).toEqual({ sx: 8, sy: 8, sw: 464, sh: 164, outW: 464, outH: 164 });
  });
  it('caps a big crop at 1568 on the long edge', () => {
    const p = planCrop({ x: 0, y: 0, w: 1280, h: 800 }, vp, { w: 2560, h: 1600 });
    expect(p.outW).toBe(1568);
    expect(p.outH).toBe(980);
  });
  it('falls back to the whole capture for an off-screen rect', () => {
    const p = planCrop({ x: 5000, y: 5000, w: 10, h: 10 }, vp, { w: 1280, h: 800 });
    expect([p.sx, p.sy, p.sw, p.sh]).toEqual([0, 0, 1280, 800]);
  });
  it('plans the viewport copy with a scaled outline', () => {
    const p = planViewport({ x: 128, y: 80, w: 256, h: 160 }, vp, { w: 2560, h: 1600 });
    expect([p.outW, p.outH]).toEqual([1568, 980]);
    expect(p.outline?.x).toBeCloseTo(156.8);
    expect(p.outline?.w).toBeCloseTo(313.6);
    expect(p.lineWidth).toBeGreaterThanOrEqual(2);
    expect(planViewport({ x: -50, y: -50, w: 10, h: 10 }, vp, { w: 1280, h: 800 }).outline).toBeNull();
  });
});

describe('chips', () => {
  it('builds a chip with component first and a tooltip', () => {
    const c = buildChip(3, draft());
    expect(c).toMatchObject({ id: 3, status: 'capturing', dimmed: false, stale: false, component: 'CardHeader', label: 'div.card-header · CardHeader' });
    expect(c.tooltip).toBe('src/lib/Card.svelte:14:3 (element) · repeated 12×, instance 3');
    expect(chipTitle(c)).toBe('CardHeader');
  });
  it('falls back to tag.class and region labels', () => {
    const noComp = draft({ framework: { name: 'unknown', version: null, source: null, componentChain: [], props: {} }, repeat: null });
    const c = buildChip(1, noComp);
    expect(c.label).toBe('div.card-header');
    expect(c.tooltip).toBe('source unknown');
    const r = buildChip(2, draft({ kind: 'region', element: null, region: { rect: { x: 0, y: 0, w: 320.4, h: 180 }, elements: [] } }));
    expect(r.label).toContain('region 320×180');
  });
  it('builds pins', () => {
    const anchor = { tag: 'div', source: null, selector: 'div', altSelectors: [], textFingerprint: '', repeatIndex: null, region: null };
    expect(buildPin(1, draft(), anchor, true)).toEqual({ id: 1, anchor, label: 'CardHeader  src/lib/Card.svelte:14', dimmed: true });
  });
});

describe('insertAtRange', () => {
  it('pads and places the caret', () => {
    expect(insertAtRange('', 0, 0, '[1]')).toEqual({ text: '[1] ', caret: 4 });
    expect(insertAtRange('make', 4, 4, '[1]')).toEqual({ text: 'make [1] ', caret: 9 });
    expect(insertAtRange('make  match', 5, 5, '[1]')).toEqual({ text: 'make [1] match', caret: 8 });
    expect(insertAtRange('see .', 4, 4, '[2]')).toEqual({ text: 'see [2].', caret: 7 });
  });
});

describe('AckWaiter', () => {
  it('resolves on ack and on timeout', async () => {
    vi.useFakeTimers();
    const w = new AckWaiter();
    const a = w.wait('r1', 500);
    expect(w.ack('r1')).toBe(true);
    expect(await a).toBe(true);
    const b = w.wait('r2', 500);
    vi.advanceTimersByTime(501);
    expect(await b).toBe(false);
    expect(w.ack('r2')).toBe(false);
    vi.useRealTimers();
  });
});

describe('capture gate', () => {
  it('serialises and keeps a gap', async () => {
    let t = 0;
    const starts: number[] = [];
    const gate = createCaptureGate(600, {
      now: () => t,
      sleep: async (ms) => {
        t += ms;
      },
    });
    const job = (d: number) => async () => {
      starts.push(t);
      t += d;
    };
    await Promise.all([gate.run(job(100)), gate.run(job(100)), gate.run(job(100))]);
    expect(starts).toEqual([0, 700, 1400]);
  });
  it('survives a failing job', async () => {
    const gate = createCaptureGate(0);
    await expect(gate.run(() => Promise.reject(new Error('x')))).rejects.toThrow('x');
    expect(await gate.run(async () => 7)).toBe(7);
  });
});
