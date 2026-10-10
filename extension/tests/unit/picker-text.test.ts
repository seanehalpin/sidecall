import { describe, expect, it } from 'vitest';
import { routeOf } from '../../src/lib/page-context';
import { displayPath, isDrag, rectFromPoints, textFingerprint, trimText } from '../../src/lib/picker/util';

describe('trimText', () => {
  it('collapses whitespace and cuts with an ellipsis', () => {
    expect(trimText('  a \n\t b  ', 10)).toBe('a b');
    const t = trimText('x'.repeat(600), 500);
    expect(t.length).toBe(500);
    expect(t.endsWith('…')).toBe(true);
  });
});

describe('textFingerprint', () => {
  it('lowercases, collapses and caps at 80', () => {
    expect(textFingerprint('  Project   3 \n')).toBe('project 3');
    expect(textFingerprint('A'.repeat(200)).length).toBe(80);
  });
});

describe('route from URL (page.route = pathname+search+hash)', () => {
  it.each([
    ['http://localhost:5173/', '/'],
    ['http://localhost:5173/a/b?x=1#top', '/a/b?x=1#top'],
  ])('%s', (u, r) => expect(routeOf(u)).toBe(r));
});

describe('drag geometry', () => {
  it('treats ≤4px as a click', () => {
    expect(isDrag(3, 0)).toBe(false);
    expect(isDrag(3, 3)).toBe(true);
    expect(isDrag(0, 5)).toBe(true);
  });
  it('builds a normalised rect from any two corners', () => {
    expect(rectFromPoints(10, 50, 4, 20)).toEqual({ x: 4, y: 20, w: 6, h: 30 });
  });
});

describe('displayPath', () => {
  it.each([
    ['src/routes/components/Card.svelte', 'src/routes/components/Card.svelte'],
    ['/Users/me/app/src/components/Card.tsx', 'src/components/Card.tsx'],
    ['http://localhost:5275/src/components/Card.tsx?t=1', 'src/components/Card.tsx'],
  ])('%s', (i, o) => expect(displayPath(i)).toBe(o));
});
