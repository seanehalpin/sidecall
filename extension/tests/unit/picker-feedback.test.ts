import { describe, expect, it } from 'vitest';
import { chooseTarget } from '../../src/lib/picker/input';
import { labelPosition } from '../../src/lib/picker/overlay';

const view = { w: 1280, h: 800 };

describe('chooseTarget', () => {
  const area = (n: { a: number }) => n.a;
  const viewportArea = view.w * view.h;

  it('takes the topmost element when it is a normal size', () => {
    const stack = [{ a: 5000 }, { a: 400000 }];
    expect(chooseTarget(stack, area, viewportArea)).toBe(stack[0]);
  });

  it('skips page-sized wrappers when something smaller is underneath', () => {
    const wrapper = { a: viewportArea * 0.95 };
    const canvas = { a: 90000 };
    expect(chooseTarget([wrapper, canvas, { a: viewportArea }], area, viewportArea)).toBe(canvas);
  });

  it('falls back to the topmost when everything is huge', () => {
    const stack = [{ a: viewportArea }, { a: viewportArea * 0.9 }];
    expect(chooseTarget(stack, area, viewportArea)).toBe(stack[0]);
  });

  it('returns null for an empty stack', () => {
    expect(chooseTarget([], area, viewportArea)).toBeNull();
  });
});

describe('labelPosition', () => {
  it('sits above a box with room above', () => {
    const p = labelPosition({ x: 100, y: 200, w: 300, h: 50 }, view);
    expect(p.y).toBeLessThan(200);
    expect(p.x).toBe(100);
  });

  it('goes below a box at the top of the screen', () => {
    const p = labelPosition({ x: 100, y: 4, w: 300, h: 50 }, view);
    expect(p.y).toBe(58);
  });

  it('goes inside a box that fills the height, clamped on screen', () => {
    const p = labelPosition({ x: 220, y: 0, w: 1060, h: 800 }, view);
    expect(p.y).toBeGreaterThanOrEqual(0);
    expect(p.y).toBeLessThan(800 - 24);
    expect(p.x).toBeGreaterThanOrEqual(4);
  });

  it('handles a box that starts above the viewport', () => {
    const p = labelPosition({ x: -50, y: -300, w: 400, h: 1200 }, view);
    expect(p.x).toBeGreaterThanOrEqual(4);
    expect(p.y).toBeGreaterThanOrEqual(0);
  });

  it('keeps a label near the right edge on screen', () => {
    const p = labelPosition({ x: 1270, y: 300, w: 200, h: 40 }, view);
    expect(p.x).toBeLessThanOrEqual(view.w - 140);
  });
});
