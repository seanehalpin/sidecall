import { describe, expect, it } from 'vitest';
import { costRows, formatUsd, totalCost } from '../../src/lib/costs';

describe('costs', () => {
  it('formats dollars', () => {
    expect(formatUsd(0)).toBe('$0.00');
    expect(formatUsd(0.004)).toBe('<$0.01');
    expect(formatUsd(0.4249)).toBe('$0.42');
    expect(formatUsd(12.3)).toBe('$12.30');
  });

  it('totals and breaks down', () => {
    const c = { talker: 0.1, call: 0.02, worker: 1.5, since: 0 };
    expect(totalCost(c)).toBeCloseTo(1.62);
    expect(costRows(c).map((r) => r.label)).toEqual(['Chat', 'Calls', 'Claude Code']);
  });
});
