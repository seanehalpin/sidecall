// Overlay hide/show handshake: the worker asks the page to hide its picker UI and
// waits for the matching 'overlay-ack' (bounded), so captureVisibleTab is clean.

export const OVERLAY_ACK_TIMEOUT_MS = 500;

export class AckWaiter {
  private pending = new Map<string, { resolve: (acked: boolean) => void; timer: ReturnType<typeof setTimeout> }>();

  /** Resolves true on the matching ack, false on timeout or cancel. */
  wait(reqId: string, timeoutMs: number = OVERLAY_ACK_TIMEOUT_MS): Promise<boolean> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(reqId);
        resolve(false);
      }, timeoutMs);
      this.pending.set(reqId, { resolve, timer });
    });
  }

  /** Returns true when someone was waiting for this request. */
  ack(reqId: string): boolean {
    const p = this.pending.get(reqId);
    if (!p) return false;
    clearTimeout(p.timer);
    this.pending.delete(reqId);
    p.resolve(true);
    return true;
  }

  cancel(reqId: string): void {
    const p = this.pending.get(reqId);
    if (!p) return;
    clearTimeout(p.timer);
    this.pending.delete(reqId);
    p.resolve(false);
  }
}
