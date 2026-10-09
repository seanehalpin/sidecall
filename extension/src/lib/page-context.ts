// Page context for user turns: which page the user is looking at, plus a
// screenshot of the visible viewport. The service worker gathers it at send
// time; these are the pure parts.
import { allowedOrigin } from './origin';
import type { PageContext } from './protocol';

/** Claude downsizes images beyond this on the long edge anyway. */
export const SCREENSHOT_MAX_EDGE = 1568;
export const SCREENSHOT_QUALITY = 0.8;
/** captureVisibleTab + downscale + upload must finish within this, or the turn goes without. */
export const SCREENSHOT_TIMEOUT_MS = 2_000;

/** pathname + search + hash, or null for an unparseable URL. */
export function routeOf(url: string): string | null {
  try {
    const u = new URL(url);
    return `${u.pathname}${u.search}${u.hash}`;
  } catch {
    return null;
  }
}

/** Size to scale an image to so its long edge is at most `maxEdge` (never upscales). */
export function downscaleSize(
  width: number,
  height: number,
  maxEdge: number = SCREENSHOT_MAX_EDGE,
): { width: number; height: number; scaled: boolean } {
  const long = Math.max(width, height);
  if (!(long > maxEdge) || width <= 0 || height <= 0) return { width, height, scaled: false };
  const k = maxEdge / long;
  return {
    width: Math.max(1, Math.round(width * k)),
    height: Math.max(1, Math.round(height * k)),
    scaled: true,
  };
}

/** Device pixel ratio from a capture's bitmap width and the tab's CSS width. */
export function dprOf(bitmapWidth: number | null, tabWidth: number): number {
  if (!bitmapWidth || !tabWidth) return 1;
  return Math.round((bitmapWidth / tabWidth) * 100) / 100;
}

export interface TabInfo {
  url?: string;
  title?: string;
  width?: number;
  height?: number;
}

/**
 * Page context for a tab, or null when the tab isn't on the origin the rail is
 * bound to (another site, another local app, a browser page).
 */
export function pageContextFor(
  tab: TabInfo,
  boundOrigin: string | null,
  capture: { bitmapWidth: number | null; ref: string | null } = { bitmapWidth: null, ref: null },
): PageContext | null {
  if (!tab.url || !boundOrigin || allowedOrigin(tab.url) !== boundOrigin) return null;
  const route = routeOf(tab.url);
  if (route === null) return null;
  const w = tab.width ?? 0;
  const h = tab.height ?? 0;
  return {
    url: tab.url,
    route,
    title: tab.title ?? '',
    viewport: { w, h, dpr: dprOf(capture.bitmapWidth, w) },
    screenshot: capture.ref,
  };
}

/** Reject after `ms`, so a slow capture or upload never holds up a turn. */
export function withTimeout<T>(promise: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms} ms`)), ms);
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e: unknown) => {
        clearTimeout(timer);
        reject(e instanceof Error ? e : new Error(String(e)));
      },
    );
  });
}
