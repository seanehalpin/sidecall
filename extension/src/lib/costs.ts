// Display helpers for the daemon's costs.state (what this repo's Claude turns have cost).
import type { Costs } from './protocol';

/** "$0.42", "$12.30", "<$0.01" for a tiny non-zero amount, "$0.00" for none. */
export function formatUsd(usd: number): string {
  if (usd > 0 && usd < 0.01) return '<$0.01';
  return `$${usd.toFixed(2)}`;
}

export function totalCost(c: Costs): number {
  return c.talker + c.call + c.worker;
}

/** The breakdown rows shown under the total, in menu order. */
export function costRows(c: Costs): { label: string; usd: number }[] {
  return [
    { label: 'Chat', usd: c.talker },
    { label: 'Calls', usd: c.call },
    { label: 'Claude Code', usd: c.worker },
  ];
}
