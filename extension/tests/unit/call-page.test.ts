import { describe, expect, it } from 'vitest';
import { CALL_PAGE_MIN_INTERVAL_MS, callPageSent, callPageStep, initialCallPageState, type CallPageEvent, type CallPageState } from '../../src/lib/call-page';

function run(events: [CallPageEvent, number][]): string[] {
  let s: CallPageState = initialCallPageState;
  const out: string[] = [];
  for (const [ev, t] of events) {
    const r = callPageStep(s, ev, t);
    s = r.state;
    out.push(r.action.kind === 'after' ? `after:${r.action.ms}` : r.action.kind);
    if (r.action.kind === 'now') s = callPageSent(s, t);
  }
  return out;
}

describe('call.page triggers', () => {
  it('sends on listening and on the first partial of an utterance only', () => {
    expect(run([['listening', 0], ['partial', 5000], ['partial', 6000], ['final', 7000], ['partial', 9500]])).toEqual(['now', 'now', 'none', 'none', 'now']);
  });
  it('debounces to one per interval with a trailing send', () => {
    expect(run([['listening', 0], ['tab', 500], ['partial', 1500]])).toEqual(['now', `after:${CALL_PAGE_MIN_INTERVAL_MS - 500}`, 'after:500']);
  });
  it('tab changes always trigger', () => {
    expect(run([['tab', 0], ['tab', 3000]])).toEqual(['now', 'now']);
  });
});
