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

/**
 * Picks to dim after a user transcript.final (SPEC §6.9). Voice turns are bound
 * to picks by the daemon, so the worker only learns which picks a spoken turn
 * used from the final transcript. Typed turns already dimmed theirs on send, so
 * only live picks that are still undimmed are returned; agent turns dim nothing.
 */
export function picksToDimForTurn(
  live: readonly { id: number; dimmed: boolean }[],
  turn: { role: string; pickIds: readonly number[] },
): number[] {
  if (turn.role !== 'user' || !turn.pickIds.length) return [];
  const wanted = new Set(turn.pickIds);
  return live.filter((p) => !p.dimmed && wanted.has(p.id)).map((p) => p.id).sort((a, b) => a - b);
}
