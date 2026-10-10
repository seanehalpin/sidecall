/**
 * When to send `call.page` during a voice call (SPEC §5.6): on reaching listening,
 * on a tab change, and when a new utterance starts (the first partial after a final
 * or after listening). At most one per `CALL_PAGE_MIN_INTERVAL_MS`; a trigger inside
 * the window schedules one trailing send at its end.
 */
export const CALL_PAGE_MIN_INTERVAL_MS = 2000;

export type CallPageEvent = 'listening' | 'tab' | 'partial' | 'final';

export interface CallPageState {
  lastSentAt: number | null;
  /** The next partial starts a new utterance. */
  awaitingUtterance: boolean;
}

export type CallPageAction = { kind: 'none' } | { kind: 'now' } | { kind: 'after'; ms: number };

export const initialCallPageState: CallPageState = { lastSentAt: null, awaitingUtterance: true };

export function callPageStep(
  state: CallPageState,
  ev: CallPageEvent,
  now: number,
): { state: CallPageState; action: CallPageAction } {
  if (ev === 'final') return { state: { ...state, awaitingUtterance: true }, action: { kind: 'none' } };
  let next = state;
  if (ev === 'listening') next = { ...state, awaitingUtterance: true };
  if (ev === 'partial') {
    if (!state.awaitingUtterance) return { state, action: { kind: 'none' } };
    next = { ...state, awaitingUtterance: false };
  }
  const wait = state.lastSentAt === null ? 0 : state.lastSentAt + CALL_PAGE_MIN_INTERVAL_MS - now;
  return { state: next, action: wait <= 0 ? { kind: 'now' } : { kind: 'after', ms: wait } };
}

/** Record a send at `now`. */
export function callPageSent(state: CallPageState, now: number): CallPageState {
  return { ...state, lastSentAt: now };
}
