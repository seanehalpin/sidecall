// Reconnect backoff for the daemon WebSocket (SPEC §5.6): 1 s doubling to 30 s.

export const BACKOFF_MIN_MS = 1_000;
export const BACKOFF_MAX_MS = 30_000;

/** Delay before reconnect attempt number `attempt` (0-based: first retry is 0). */
export function backoffDelay(attempt: number): number {
  const n = Number.isFinite(attempt) ? Math.max(0, Math.floor(attempt)) : 0;
  // Cap the exponent so 2 ** n never overflows into Infinity.
  return Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** Math.min(n, 16));
}
