// Small pure helpers shared by the picker modules.
import type { Rect } from '../protocol.gen';

export const collapseWs = (s: string): string => s.replace(/\s+/g, ' ').trim();

/** Whitespace-collapsed text, cut to `max` characters (the last one an ellipsis when cut). */
export function trimText(s: string, max: number): string {
  const t = collapseWs(s);
  return t.length > max ? `${t.slice(0, Math.max(0, max - 1))}…` : t;
}

/** Normalised leading text used to recognise an element after the DOM was rebuilt. */
export function textFingerprint(s: string): string {
  return collapseWs(s).toLowerCase().slice(0, 80);
}

export const stripQuery = (u: string): string => u.replace(/[?#].*$/, '');

export function rectOf(r: { x: number; y: number; width: number; height: number }): Rect {
  return { x: round(r.x), y: round(r.y), w: round(r.width), h: round(r.height) };
}

export const round = (n: number): number => Math.round(n * 10) / 10;

export function rectsIntersect(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

export function intersectionArea(a: Rect, b: Rect): number {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

/** Rect spanned by two points. */
export function rectFromPoints(x1: number, y1: number, x2: number, y2: number): Rect {
  return { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) };
}

/** A press that moved no more than this many pixels is a click, otherwise a drag. */
export const DRAG_THRESHOLD_PX = 4;
export const isDrag = (dx: number, dy: number): boolean => Math.hypot(dx, dy) > DRAG_THRESHOLD_PX;

let warned = false;
/** Log a picker failure once, with the [sidecall] prefix; never throws. */
export function warnOnce(where: string, err: unknown): void {
  if (warned) return;
  warned = true;
  try {
    console.warn(`[sidecall] picker error in ${where}`, err);
  } catch {
    /* ignore */
  }
}

/** Wrap an event handler so a bug in the picker can never throw into the page. */
export function guard<A extends unknown[]>(where: string, fn: (...a: A) => void): (...a: A) => void {
  return (...a: A) => {
    try {
      fn(...a);
    } catch (err) {
      warnOnce(where, err);
    }
  };
}

/** Last two path segments, for labels: "src/lib/Card.svelte". */
export function displayPath(file: string): string {
  let p = stripQuery(file);
  p = p.replace(/^[a-z]+:\/\/[^/]+/i, '');
  if (p.startsWith('src/')) return p;
  const i = p.lastIndexOf('/src/');
  if (i >= 0) return p.slice(i + 1);
  const parts = p.split('/').filter(Boolean);
  return parts.slice(-2).join('/');
}

export const OVERLAY_HOST_ID = 'sidecall-picker-root';
