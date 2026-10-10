// Pin registry: numbered pins anchored to elements/regions, re-anchored when the DOM changes
// (HMR, re-render) or after a reload (SPEC §5.2).
import type { Anchor, PageToExt, PinSpec } from '../picker-protocol';
import type { Rect } from '../protocol.gen';
import { reanchor, type AnchorEnv } from './anchor';
import type { Overlay, PinState, PinView } from './overlay';
import { rectOf } from './util';

/** After a restore the page may still be rendering; don't call a pin stale before this. */
export const RESTORE_GRACE_MS = 4000;
export const REANCHOR_DEBOUNCE_MS = 120;
export const REANCHOR_MAX_WAIT_MS = 1000;

interface Entry {
  id: number;
  spec: PinSpec;
  anchor: Anchor;
  el: Element | null;
  /** Region pins: document coordinates. */
  regionDoc: Rect | null;
  status: 'ok' | 'stale';
  /** Last known document-space rect, so a stale pin stays where it was. */
  lastDoc: Rect | null;
  dimmed: boolean;
  graceUntil: number;
  reported: { status: 'ok' | 'stale'; el: Element | null } | null;
}

export interface PinsDeps {
  overlay: Overlay;
  post(msg: PageToExt): void;
  env: AnchorEnv<Element>;
  /** Re-resolve an anchor for a re-found element (selector/source may have changed). */
  refreshAnchor(el: Element, prev: Anchor): Promise<Anchor>;
  now?(): number;
}

export class PinRegistry {
  private readonly pins = new Map<number, Entry>();
  private highlighted: number | null = null;
  private reanchorTimer: ReturnType<typeof setTimeout> | null = null;
  private firstDirtyAt = 0;
  private running = false;
  private again = false;
  private graceTimer: ReturnType<typeof setTimeout> | null = null;
  private layoutRaf = 0;
  private readonly now: () => number;

  constructor(private readonly deps: PinsDeps) {
    this.now = deps.now ?? (() => Date.now());
  }

  get size(): number {
    return this.pins.size;
  }
  /** Pins from the current turn (the ones Esc clears). */
  get hasLive(): boolean {
    for (const p of this.pins.values()) if (!p.dimmed) return true;
    return false;
  }
  has(id: number): boolean {
    return this.pins.has(id);
  }
  /** Test/debug view. */
  snapshot(): { id: number; status: string; dimmed: boolean; text: string | null }[] {
    return [...this.pins.values()].map((p) => ({
      id: p.id,
      status: p.status,
      dimmed: p.dimmed,
      text: p.el ? (p.el.textContent ?? '').trim().slice(0, 40) : null,
    }));
  }

  // --- commands from the worker --------------------------------------------------------

  /** A pick we made ourselves was accepted: we already hold its element. */
  addLocal(spec: PinSpec, el: Element | null, regionDoc: Rect | null): void {
    const e: Entry = {
      id: spec.id,
      spec,
      anchor: spec.anchor,
      el,
      regionDoc: regionDoc ?? spec.anchor.region,
      status: 'ok',
      lastDoc: null,
      dimmed: spec.dimmed,
      graceUntil: 0,
      reported: { status: 'ok', el },
    };
    this.pins.set(spec.id, e);
    this.requestLayout();
    // Resolve anyway if we don't hold the element (pin-add for a pick we don't remember).
    if (!el && !regionDoc && !spec.anchor.region) this.markPendingResolve(e);
  }

  /** After a reload: draw and re-anchor every live pin. */
  restore(specs: PinSpec[]): void {
    const t = this.now();
    for (const spec of specs) {
      const old = this.pins.get(spec.id);
      this.pins.set(spec.id, {
        id: spec.id,
        spec,
        anchor: spec.anchor,
        el: old?.el ?? null,
        regionDoc: spec.anchor.region,
        status: 'ok',
        lastDoc: old?.lastDoc ?? null,
        dimmed: spec.dimmed,
        graceUntil: t + RESTORE_GRACE_MS,
        reported: null,
      });
    }
    this.scheduleReanchor(true);
  }

  remove(id: number): void {
    this.pins.delete(id);
    if (this.highlighted === id) this.highlighted = null;
    this.requestLayout();
  }

  clear(): void {
    this.pins.clear();
    this.highlighted = null;
    this.requestLayout();
  }

  dim(ids: number[], dimmed: boolean): void {
    for (const id of ids) {
      const p = this.pins.get(id);
      if (p) p.dimmed = dimmed;
    }
    this.requestLayout();
  }

  highlight(id: number | null): void {
    this.highlighted = id;
    this.requestLayout();
  }

  // --- re-anchoring ----------------------------------------------------------------------

  private markPendingResolve(e: Entry): void {
    e.graceUntil = this.now() + RESTORE_GRACE_MS;
    e.reported = null;
    this.scheduleReanchor(true);
  }

  /** Debounced: DOM mutations come in bursts (HMR rebuilds a whole subtree). */
  scheduleReanchor(immediate = false): void {
    if (this.pins.size === 0) return;
    const t = this.now();
    if (!this.reanchorTimer) this.firstDirtyAt = t;
    if (this.reanchorTimer) clearTimeout(this.reanchorTimer);
    const wait = immediate ? 0 : Math.max(0, Math.min(REANCHOR_DEBOUNCE_MS, this.firstDirtyAt + REANCHOR_MAX_WAIT_MS - t));
    this.reanchorTimer = setTimeout(() => {
      this.reanchorTimer = null;
      void this.reanchorAll();
    }, wait);
  }

  async reanchorAll(): Promise<void> {
    if (this.running) {
      this.again = true;
      return;
    }
    this.running = true;
    try {
      do {
        this.again = false;
        for (const e of [...this.pins.values()]) {
          if (!this.pins.has(e.id)) continue;
          await this.reanchorOne(e);
        }
      } while (this.again);
    } finally {
      this.running = false;
    }
    this.requestLayout();
    this.armGraceTimer();
  }

  private async reanchorOne(e: Entry): Promise<void> {
    const res = await reanchor(e.anchor, e.el, this.deps.env);
    const pending = this.now() < e.graceUntil;
    if (res.status === 'stale') {
      if (pending && !e.reported) return; // still rendering after a reload: wait quietly
      e.status = 'stale';
      this.report(e);
      return;
    }
    const changed = res.el !== e.el;
    e.status = 'ok';
    e.el = res.el;
    if (res.el) {
      // Re-found by something other than identity: refresh selector/source for next time.
      if (changed && res.how !== 'kept') e.anchor = await this.deps.refreshAnchor(res.el, e.anchor);
      e.regionDoc = null;
    } else {
      e.regionDoc = e.anchor.region;
    }
    this.report(e);
  }

  private report(e: Entry): void {
    const prev = e.reported;
    if (prev && prev.status === e.status && prev.el === e.el) return;
    e.reported = { status: e.status, el: e.el };
    const rect = this.viewportRect(e);
    this.deps.post({ t: 'pin-status', id: e.id, status: e.status, rect });
  }

  /** Make sure pins that were still pending at the end of the grace period get finalised. */
  private armGraceTimer(): void {
    if (this.graceTimer) clearTimeout(this.graceTimer);
    this.graceTimer = null;
    const t = this.now();
    let next = Infinity;
    for (const e of this.pins.values()) if (!e.reported && e.graceUntil > t) next = Math.min(next, e.graceUntil);
    if (next === Infinity) {
      // Anything still unreported with an expired grace period is simply stale.
      for (const e of this.pins.values()) if (!e.reported) this.scheduleReanchor(true);
      return;
    }
    this.graceTimer = setTimeout(() => this.scheduleReanchor(true), next - t + 20);
  }

  // --- drawing ---------------------------------------------------------------------------

  private viewportRect(e: Entry): Rect | null {
    if (e.el && e.el.isConnected) {
      const r = rectOf(e.el.getBoundingClientRect());
      e.lastDoc = { x: r.x + window.scrollX, y: r.y + window.scrollY, w: r.w, h: r.h };
      return r;
    }
    const doc = e.regionDoc ?? e.lastDoc;
    if (!doc) return null;
    return { x: doc.x - window.scrollX, y: doc.y - window.scrollY, w: doc.w, h: doc.h };
  }

  requestLayout(): void {
    if (this.layoutRaf) return;
    this.layoutRaf = requestAnimationFrame(() => {
      this.layoutRaf = 0;
      this.layout();
    });
  }

  layout(): void {
    const views: PinView[] = [];
    for (const e of this.pins.values()) {
      const pendingQuiet = !e.reported && this.now() < e.graceUntil && !e.el && !e.regionDoc;
      let state: PinState = 'normal';
      if (e.status === 'stale') state = 'stale';
      else if (this.highlighted === e.id) state = 'highlighted';
      else if (e.dimmed) state = 'dimmed';
      views.push({ id: e.id, rect: pendingQuiet ? null : this.viewportRect(e), state });
    }
    this.deps.overlay.renderPins(views);
  }
}
