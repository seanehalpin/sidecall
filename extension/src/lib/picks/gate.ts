// captureVisibleTab is limited to ~2 calls/s: serialise captures and keep a gap
// between them. Shared by pick captures and the per-turn page screenshot.

export const CAPTURE_GAP_MS = 600;

export interface GateClock {
  now(): number;
  sleep(ms: number): Promise<void>;
}

const realClock: GateClock = {
  now: () => Date.now(),
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
};

export function createCaptureGate(gapMs: number = CAPTURE_GAP_MS, clock: GateClock = realClock) {
  let tail: Promise<unknown> = Promise.resolve();
  let lastEnd = Number.NEGATIVE_INFINITY;
  return {
    /** Run `fn` after earlier jobs finish and at least `gapMs` after the last one ended. */
    run<T>(fn: () => Promise<T>): Promise<T> {
      const job = tail.then(async () => {
        const wait = lastEnd + gapMs - clock.now();
        if (wait > 0) await clock.sleep(wait);
        try {
          return await fn();
        } finally {
          lastEnd = clock.now();
        }
      });
      tail = job.catch(() => {});
      return job;
    },
  };
}
