import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  SCREENSHOT_MAX_EDGE,
  downscaleSize,
  dprOf,
  pageContextFor,
  routeOf,
  withTimeout,
} from '../../src/lib/page-context';

describe('routeOf', () => {
  it.each([
    ['http://localhost:5173/', '/'],
    ['http://localhost:5173', '/'],
    ['http://localhost:5173/settings/profile', '/settings/profile'],
    ['http://localhost:5173/a?tab=b&x=1#top', '/a?tab=b&x=1#top'],
    ['http://127.0.0.1:3000/#/hash/route', '/#/hash/route'],
  ])('%s → %s', (url, route) => {
    expect(routeOf(url)).toBe(route);
  });

  it('rejects garbage', () => {
    expect(routeOf('not a url')).toBeNull();
  });
});

describe('downscaleSize', () => {
  it('leaves small images alone', () => {
    expect(downscaleSize(1440, 900)).toEqual({ width: 1440, height: 900, scaled: false });
    expect(downscaleSize(SCREENSHOT_MAX_EDGE, 800)).toEqual({ width: SCREENSHOT_MAX_EDGE, height: 800, scaled: false });
  });

  it('scales the long edge down to the max, keeping the aspect ratio', () => {
    expect(downscaleSize(2880, 1800)).toEqual({ width: 1568, height: 980, scaled: true });
    expect(downscaleSize(1000, 3136)).toEqual({ width: 500, height: 1568, scaled: true });
    expect(downscaleSize(4000, 10, 1000)).toEqual({ width: 1000, height: 3, scaled: true });
    expect(downscaleSize(100000, 1, 1000).height).toBe(1);
  });

  it('ignores degenerate sizes', () => {
    expect(downscaleSize(0, 0).scaled).toBe(false);
    expect(downscaleSize(Number.NaN, 10).scaled).toBe(false);
  });
});

describe('dprOf', () => {
  it('divides bitmap width by tab width', () => {
    expect(dprOf(2880, 1440)).toBe(2);
    expect(dprOf(1440, 1440)).toBe(1);
    expect(dprOf(1801, 1200)).toBe(1.5);
  });
  it('defaults to 1 when unknown', () => {
    expect(dprOf(null, 1440)).toBe(1);
    expect(dprOf(2880, 0)).toBe(1);
  });
});

describe('pageContextFor', () => {
  const tab = { url: 'http://localhost:5173/settings?tab=a#x', title: 'Settings', width: 1440, height: 900 };

  it('builds the context for a tab on the bound origin', () => {
    expect(pageContextFor(tab, 'http://localhost:5173', { bitmapWidth: 2880, ref: 'a'.repeat(32) })).toEqual({
      url: tab.url,
      route: '/settings?tab=a#x',
      title: 'Settings',
      viewport: { w: 1440, h: 900, dpr: 2 },
      screenshot: 'a'.repeat(32),
    });
  });

  it('has no screenshot when there was no capture', () => {
    expect(pageContextFor(tab, 'http://localhost:5173')).toMatchObject({ screenshot: null, viewport: { dpr: 1 } });
  });

  it('is null off the bound origin', () => {
    expect(pageContextFor(tab, 'http://localhost:3000')).toBeNull();
    expect(pageContextFor(tab, null)).toBeNull();
    expect(pageContextFor({ ...tab, url: 'https://example.com/' }, 'http://localhost:5173')).toBeNull();
    expect(pageContextFor({ ...tab, url: undefined }, 'http://localhost:5173')).toBeNull();
    expect(pageContextFor({ ...tab, url: 'chrome://newtab/' }, 'http://localhost:5173')).toBeNull();
  });

  it('tolerates missing title and size', () => {
    expect(pageContextFor({ url: 'http://localhost:5173/' }, 'http://localhost:5173')).toEqual({
      url: 'http://localhost:5173/',
      route: '/',
      title: '',
      viewport: { w: 0, h: 0, dpr: 1 },
      screenshot: null,
    });
  });
});

describe('withTimeout', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('resolves and rejects like the promise when it is quick', async () => {
    await expect(withTimeout(Promise.resolve(3), 100, 'x')).resolves.toBe(3);
    await expect(withTimeout(Promise.reject(new Error('boom')), 100, 'x')).rejects.toThrow('boom');
  });

  it('rejects when the promise is too slow', async () => {
    vi.useFakeTimers();
    const slow = withTimeout(new Promise(() => {}), 2000, 'screenshot');
    vi.advanceTimersByTime(2000);
    await expect(slow).rejects.toThrow('screenshot timed out after 2000 ms');
  });
});
