import { describe, expect, it } from 'vitest';
import { safeSerializeProps } from '../../src/lib/picker/serialize';

describe('safeSerializeProps', () => {
  it('returns null for non-objects', () => {
    expect(safeSerializeProps(null)).toBeNull();
    expect(safeSerializeProps(undefined)).toBeNull();
    expect(safeSerializeProps('x')).toBeNull();
  });

  it('keeps plain data and names functions', () => {
    function onClick() {}
    const out = safeSerializeProps({ title: 'Hi', n: 3, ok: true, onClick, cb: () => {} });
    expect(out).toEqual({ title: 'Hi', n: 3, ok: true, onClick: '[fn onClick]', cb: '[fn cb]' });
  });

  it('guards cycles but not shared references', () => {
    const a: Record<string, unknown> = { name: 'a' };
    a.self = a;
    const shared = { v: 1 };
    const out = safeSerializeProps({ a, x: shared, y: shared });
    expect(out).toEqual({ a: { name: 'a', self: '[Circular]' }, x: { v: 1 }, y: { v: 1 } });
  });

  it('limits depth to 3', () => {
    const out = safeSerializeProps({ l1: { l2: { l3: { l4: 1 } } } });
    expect(out).toEqual({ l1: { l2: { l3: '[Object]' } } });
  });

  it('summarises React elements, Maps and long arrays', () => {
    const el = { $$typeof: Symbol.for('react.element'), type: 'div' };
    const out = safeSerializeProps({ el, m: new Map([[1, 2]]), arr: Array.from({ length: 50 }, (_, i) => i) });
    expect(out!.el).toBe('[ReactElement div]');
    expect(out!.m).toBe('[Map(1)]');
    expect((out!.arr as unknown[]).length).toBe(21);
  });

  it('caps the JSON at 4 KB', () => {
    const big: Record<string, string> = {};
    for (let i = 0; i < 30; i++) big[`key${i}`] = 'x'.repeat(190);
    const out = safeSerializeProps(big)!;
    expect(JSON.stringify(out).length).toBeLessThanOrEqual(4096);
    expect(out['…']).toBeDefined();
  });

  it('survives throwing getters', () => {
    const o = {
      get bad(): string {
        throw new Error('no');
      },
      ok: 1,
    };
    expect(safeSerializeProps(o)).toEqual({ bad: '[getter threw]', ok: 1 });
  });
});
