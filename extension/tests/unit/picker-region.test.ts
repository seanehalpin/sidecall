import { describe, expect, it } from 'vitest';
import { rankRegionElements } from '../../src/lib/picker/region';

const c = (name: string, x: number, y: number, w: number, h: number) => ({ item: name, rect: { x, y, w, h } });

describe('rankRegionElements', () => {
  const region = { x: 0, y: 0, w: 300, h: 100 };

  it('drops elements outside the region', () => {
    const out = rankRegionElements(region, [c('in', 10, 10, 50, 50), c('out', 500, 500, 50, 50)]);
    expect(out.map((o) => o.item)).toEqual(['in']);
  });

  it('prefers things the region covers over a big container it only clips', () => {
    const container = c('page', 0, 0, 3000, 3000);
    const cards = [c('c1', 0, 0, 90, 90), c('c2', 100, 0, 90, 90)];
    const out = rankRegionElements(region, [container, ...cards], 2);
    expect(out.map((o) => o.item)).toEqual(['c1', 'c2']);
  });

  it('keeps document order and caps at 20', () => {
    const many = Array.from({ length: 40 }, (_, i) => c(`e${i}`, i, 0, 5, 5));
    const out = rankRegionElements(region, many);
    expect(out.length).toBe(20);
    const idx = out.map((o) => Number(o.item.slice(1)));
    expect([...idx].sort((a, b) => a - b)).toEqual(idx);
  });

  it('outer fully-covered elements beat their children when the cap bites', () => {
    const cands = [c('card', 0, 0, 100, 100), ...Array.from({ length: 30 }, (_, i) => c(`kid${i}`, 1, 1, 10, 10))];
    expect(rankRegionElements(region, cands, 3).map((o) => o.item)).toContain('card');
  });
});
