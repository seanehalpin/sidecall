import { describe, expect, it } from 'vitest';
import { RingBuffer, formatConsoleError, formatErrorEvent, formatRejection } from '../../src/lib/picker/console-capture';

describe('RingBuffer', () => {
  it('keeps only the newest N', () => {
    const b = new RingBuffer<number>(3);
    for (let i = 1; i <= 5; i++) b.push(i);
    expect(b.toArray()).toEqual([3, 4, 5]);
  });
});

describe('error formatting', () => {
  it('joins console.error args', () => {
    expect(formatConsoleError(['Failed:', { a: 1 }, 3])).toBe('Failed: {"a":1} 3');
  });
  it('does not throw on circular objects', () => {
    const o: Record<string, unknown> = {};
    o.o = o;
    expect(formatConsoleError([o])).toBe('[unserialisable]');
  });
  it('caps long entries', () => {
    expect(formatConsoleError(['x'.repeat(2000)]).length).toBeLessThanOrEqual(500);
  });
  it('formats error events and rejections', () => {
    expect(formatErrorEvent({ message: 'boom', filename: 'http://x/a.js', lineno: 7 })).toBe('boom (http://x/a.js:7)');
    expect(formatRejection(new Error('nope'))).toContain('Unhandled rejection: ');
  });
});
