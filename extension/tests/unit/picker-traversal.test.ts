import { describe, expect, it } from 'vitest';
import { currentOf, moveDown, moveUp, originOf, startPath } from '../../src/lib/picker/traversal';

// A tiny tree: root > mid > leaf
const parents: Record<string, string | null> = { leaf: 'mid', mid: 'root', root: null };
const parentOf = (t: string) => parents[t] ?? null;

describe('selection path stack', () => {
  it('starts on the element under the pointer', () => {
    expect(currentOf(startPath('leaf'))).toBe('leaf');
  });

  it('↑ climbs to the parent, and stops at the top', () => {
    let p = startPath('leaf');
    p = moveUp(p, parentOf);
    expect(currentOf(p)).toBe('mid');
    p = moveUp(p, parentOf);
    expect(currentOf(p)).toBe('root');
    expect(moveUp(p, parentOf)).toBe(p);
  });

  it('↓ walks back down the original path and stops at the origin', () => {
    let p = moveUp(moveUp(startPath('leaf'), parentOf), parentOf);
    p = moveDown(p);
    expect(currentOf(p)).toBe('mid');
    p = moveDown(p);
    expect(currentOf(p)).toBe('leaf');
    expect(moveDown(p)).toBe(p);
    expect(originOf(p)).toBe('leaf');
  });

  it('going up again after down reuses the recorded chain', () => {
    const up2 = moveUp(moveUp(startPath('leaf'), parentOf), parentOf);
    const p = moveUp(moveDown(up2), () => {
      throw new Error('should not ask for a new parent');
    });
    expect(currentOf(p)).toBe('root');
  });

  it('is immutable', () => {
    const p = startPath('leaf');
    moveUp(p, parentOf);
    expect(currentOf(p)).toBe('leaf');
  });
});
