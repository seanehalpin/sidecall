// Which elements a rubber-band region covers (SPEC §5.3: "top 20 intersecting").
import type { Rect } from '../protocol.gen';
import { intersectionArea } from './util';

export const REGION_MAX_ELEMENTS = 20;

export interface RegionCandidate<T> {
  item: T;
  rect: Rect;
}

/**
 * Keep elements that intersect the region, ranked by how much of *themselves* the region
 * covers (so a card inside the band beats the page container around it), larger first on
 * ties (outer cards before their children). Returned in the input (document) order.
 */
export function rankRegionElements<T>(
  region: Rect,
  candidates: RegionCandidate<T>[],
  max = REGION_MAX_ELEMENTS,
): RegionCandidate<T>[] {
  const scored = candidates
    .map((c, order) => {
      const area = c.rect.w * c.rect.h;
      const inter = intersectionArea(region, c.rect);
      return { c, order, area, score: area > 0 ? inter / area : 0, inter };
    })
    .filter((s) => s.inter > 0 && s.area > 0);
  scored.sort((a, b) => b.score - a.score || b.area - a.area || a.order - b.order);
  return scored
    .slice(0, max)
    .sort((a, b) => a.order - b.order)
    .map((s) => s.c);
}
