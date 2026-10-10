// Pure rect/scale math for pick screenshots (SPEC §5.3). `Rect`s are viewport CSS
// pixels; capture bitmaps are device pixels (dpr = bitmap width / viewport width).
import { SCREENSHOT_MAX_EDGE, downscaleSize } from '../page-context';
import type { Rect } from '../protocol';

export const CROP_MARGIN = 16;
export const OUTLINE_WIDTH = 3;

export interface Size {
  w: number;
  h: number;
}

/** `rect` grown by `margin` on every side and clipped to the viewport; null when nothing is left. */
export function clipRect(rect: Rect, viewport: Size, margin: number = 0): Rect | null {
  const x1 = Math.max(0, rect.x - margin);
  const y1 = Math.max(0, rect.y - margin);
  const x2 = Math.min(viewport.w, rect.x + rect.w + margin);
  const y2 = Math.min(viewport.h, rect.y + rect.h + margin);
  if (!(x2 > x1) || !(y2 > y1)) return null;
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

export interface CropPlan {
  /** Source rect in bitmap pixels. */
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  /** Output size (native resolution, capped on the long edge). */
  outW: number;
  outH: number;
}

/**
 * Crop = element/region rect + margin, clipped to the viewport, at native
 * resolution but capped to `maxEdge` on the long edge. A rect entirely outside
 * the viewport falls back to the whole capture.
 */
export function planCrop(
  rect: Rect,
  viewport: Size,
  bitmap: Size,
  margin: number = CROP_MARGIN,
  maxEdge: number = SCREENSHOT_MAX_EDGE,
): CropPlan {
  const kx = bitmap.w / viewport.w;
  const ky = bitmap.h / viewport.h;
  const css = clipRect(rect, viewport, margin) ?? { x: 0, y: 0, w: viewport.w, h: viewport.h };
  const sx = Math.min(bitmap.w - 1, Math.max(0, Math.floor(css.x * kx)));
  const sy = Math.min(bitmap.h - 1, Math.max(0, Math.floor(css.y * ky)));
  const sw = Math.max(1, Math.min(bitmap.w - sx, Math.ceil((css.x + css.w) * kx) - sx));
  const sh = Math.max(1, Math.min(bitmap.h - sy, Math.ceil((css.y + css.h) * ky) - sy));
  const out = downscaleSize(sw, sh, maxEdge);
  return { sx, sy, sw, sh, outW: out.width, outH: out.height };
}

export interface ViewportPlan {
  outW: number;
  outH: number;
  /** The picked rect in output pixels (clipped to the image), or null if off-screen. */
  outline: Rect | null;
  lineWidth: number;
}

/** Whole capture downscaled to <= maxEdge, with the picked rect to outline. */
export function planViewport(
  rect: Rect,
  viewport: Size,
  bitmap: Size,
  maxEdge: number = SCREENSHOT_MAX_EDGE,
): ViewportPlan {
  const out = downscaleSize(bitmap.w, bitmap.h, maxEdge);
  const visible = clipRect(rect, viewport, 0);
  const kx = out.width / viewport.w;
  const ky = out.height / viewport.h;
  const outline = visible
    ? { x: visible.x * kx, y: visible.y * ky, w: visible.w * kx, h: visible.h * ky }
    : null;
  return { outW: out.width, outH: out.height, outline, lineWidth: OUTLINE_WIDTH };
}
