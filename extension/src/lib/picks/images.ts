// Derive the two pick screenshots from ONE captureVisibleTab result (SPEC §5.3).
import { SCREENSHOT_QUALITY } from '../page-context';
import type { Rect } from '../protocol';
import { planCrop, planViewport, type Size } from './geometry';

async function encode(canvas: OffscreenCanvas): Promise<Blob> {
  return canvas.convertToBlob({ type: 'image/jpeg', quality: SCREENSHOT_QUALITY });
}

function context(canvas: OffscreenCanvas): OffscreenCanvasRenderingContext2D {
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('no 2d context');
  return ctx;
}

export interface PickImages {
  crop: Blob;
  viewport: Blob;
  cropSize: Size;
  viewportSize: Size;
}

export async function derivePickImages(capture: Blob, rect: Rect, viewport: Size): Promise<PickImages> {
  const bitmap = await createImageBitmap(capture);
  try {
    const bm = { w: bitmap.width, h: bitmap.height };

    const cp = planCrop(rect, viewport, bm);
    const cropCanvas = new OffscreenCanvas(cp.outW, cp.outH);
    context(cropCanvas).drawImage(bitmap, cp.sx, cp.sy, cp.sw, cp.sh, 0, 0, cp.outW, cp.outH);

    const vp = planViewport(rect, viewport, bm);
    const vpCanvas = new OffscreenCanvas(vp.outW, vp.outH);
    const ctx = context(vpCanvas);
    ctx.drawImage(bitmap, 0, 0, vp.outW, vp.outH);
    if (vp.outline) {
      const { x, y, w, h } = vp.outline;
      // White halo under a saturated stroke: visible on light and dark pages.
      ctx.lineJoin = 'round';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = vp.lineWidth + 2;
      ctx.strokeRect(x, y, w, h);
      ctx.strokeStyle = '#ff1744';
      ctx.lineWidth = vp.lineWidth;
      ctx.strokeRect(x, y, w, h);
    }

    return {
      crop: await encode(cropCanvas),
      viewport: await encode(vpCanvas),
      cropSize: { w: cp.outW, h: cp.outH },
      viewportSize: { w: vp.outW, h: vp.outH },
    };
  } finally {
    bitmap.close();
  }
}
