// Tracks the newest `?t=` Vite imported for each module path, to detect a stale React 19
// `_debugStack` after Fast Refresh. A PerformanceObserver sees every resource entry even when
// the resource-timing buffer is full.
import { stripQuery } from '../util';
import { frameT } from './stack';

const latest = new Map<string, number>();
let installed = false;

function note(url: string): void {
  const t = frameT(url);
  if (!t) return;
  const path = stripQuery(url);
  if ((latest.get(path) ?? 0) < t) latest.set(path, t);
}

export function installHmrTracking(): void {
  if (installed) return;
  installed = true;
  try {
    performance.getEntriesByType('resource').forEach((e) => note(e.name));
    new PerformanceObserver((list) => list.getEntries().forEach((e) => note(e.name))).observe({
      type: 'resource',
      buffered: true,
    });
  } catch {
    /* PerformanceObserver unavailable: staleness detection simply never fires */
  }
}

export const latestHmrT = (path: string): number => latest.get(stripQuery(path)) ?? 0;
