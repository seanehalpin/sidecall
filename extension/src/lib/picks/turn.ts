// Which picks a user turn carries, and the id/clock bookkeeping around picks.
import { parseMarkers } from './markers';

/**
 * The picks a turn is sent with: every live pick not yet used in a turn
 * (non-dimmed), plus any live pick the text refers to as [n] (including dimmed
 * ones from earlier turns). Ascending, de-duplicated.
 */
export function selectPickIds(live: readonly { id: number; dimmed: boolean }[], text: string): number[] {
  const ids = new Set<number>();
  for (const p of live) if (!p.dimmed) ids.add(p.id);
  const exists = new Set(live.map((p) => p.id));
  for (const id of parseMarkers(text)) if (exists.has(id)) ids.add(id);
  return [...ids].sort((a, b) => a - b);
}

/**
 * The next pick id: one past the persisted counter and past any live pick, so a
 * service-worker restart that lost the counter can never reuse an id.
 */
export function nextPickId(counter: number, liveIds: readonly number[]): number {
  return Math.max(counter, 0, ...liveIds) + 1;
}

/** Extension clock → daemon clock (SPEC §6.9); `welcome.clockOffset` = daemon_now - clockSample. */
export function daemonTime(now: number, clockOffset: number | null): number {
  return Math.round(now + (clockOffset ?? 0));
}
