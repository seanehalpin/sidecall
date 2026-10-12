// The picker's visuals: one shadow-DOM root on <html> at the highest z-index. Everything in
// it is pointer-events: none; input is captured with window-level listeners instead, only
// while ⌥ is held, so normal page interaction is never affected.
import type { Rect } from '../protocol.gen';
// The picker's colours (--studio-inspect…) come from the shared, user-editable colour file.
import COLORS from '../../styles/colors.css?inline';
import { OVERLAY_HOST_ID } from './util';

export type PinState = 'normal' | 'dimmed' | 'highlighted' | 'stale';

export interface PinView {
  id: number;
  /** Viewport rect, or null when there is nothing to draw (yet). */
  rect: Rect | null;
  state: PinState;
}

const CSS_TEXT = COLORS + `
:host { all: initial; }
.root { position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 2147483647;
  font: 11px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace; }
.box { position: absolute; box-sizing: border-box; border-radius: 2px; }
.hover { border: 2px solid var(--studio-inspect); background: var(--studio-inspectFill);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .9), inset 0 0 0 1px rgba(255, 255, 255, .55); }
.drag { border: 2px dashed var(--studio-inspect); background: var(--studio-inspectFill);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .9); }
.pending { border: 2px solid var(--studio-inspect); background: var(--studio-inspectFill);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .9); }
.label { position: absolute; max-width: 60vw; padding: 3px 7px; border-radius: 4px; background: var(--studio-inspect);
  color: var(--studio-onInspect); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, .4); }
.hint { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); padding: 6px 12px;
  border-radius: 999px; background: #1d1b2f; color: #fff; font: 12px/1.2 system-ui, sans-serif;
  white-space: nowrap; box-shadow: 0 2px 10px rgba(0, 0, 0, .45); }
.hint b { color: var(--studio-inspect); }
.pin-box { border: 1.5px solid var(--studio-inspect); }
.badge { position: absolute; top: 0; left: 0; min-width: 18px; height: 18px; padding: 0 4px; box-sizing: border-box;
  border-radius: 9px; background: var(--studio-inspect); color: var(--studio-onInspect); font-weight: 700; text-align: center; line-height: 18px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, .35); }
[data-state="dimmed"] .pin-box { border-color: color-mix(in srgb, var(--studio-inspect) 35%, transparent); }
[data-state="dimmed"] .badge { background: color-mix(in srgb, var(--studio-inspect) 55%, transparent); }
[data-state="dimmed"] { opacity: .75; }
[data-state="highlighted"] .pin-box { border-width: 3px; background: var(--studio-inspectFill); }
[data-state="highlighted"] .badge { background: var(--studio-inspectHighlight); transform: scale(1.2); }
[data-state="stale"] .pin-box { border: 1.5px dashed var(--studio-inspectStale); }
[data-state="stale"] .badge { background: var(--studio-inspectStale); }
.pin { position: absolute; top: 0; left: 0; }
`;

function div(cls: string): HTMLDivElement {
  const d = document.createElement('div');
  d.className = cls;
  return d;
}

/** Where to put the hover label so it is always on screen: above the box, else below, else inside. */
export function labelPosition(rect: Rect, view: { w: number; h: number }): { x: number; y: number } {
  const H = 24; // label height plus a gap
  const x = Math.min(Math.max(4, rect.x), Math.max(4, view.w - 140));
  if (rect.y >= H) return { x, y: rect.y - H + 2 };
  if (rect.y + rect.h + H <= view.h) return { x, y: rect.y + rect.h + 4 };
  return { x: Math.min(Math.max(4, rect.x + 6), Math.max(4, view.w - 140)), y: Math.max(rect.y, 0) + 6 };
}

function place(el: HTMLElement, r: Rect): void {
  el.style.transform = `translate(${r.x}px, ${r.y}px)`;
  el.style.width = `${r.w}px`;
  el.style.height = `${r.h}px`;
}

export class Overlay {
  readonly host: HTMLDivElement;
  private readonly root: HTMLDivElement;
  private readonly hoverBox = div('box hover');
  private readonly hoverLabel = div('label');
  private readonly dragBox = div('box drag');
  private readonly pendingBox = div('box pending');
  private readonly hint = div('hint');
  private readonly pinNodes = new Map<number, { wrap: HTMLDivElement; box: HTMLDivElement; badge: HTMLDivElement }>();
  private visible = true;

  private hintTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly HINT_HTML =
    '<b>⌥ Wilco</b> &nbsp;click to pick &middot; drag for a region &middot; Esc selects the parent';

  constructor(private readonly isActive: () => boolean = () => true) {
    this.host = document.createElement('div');
    this.host.id = OVERLAY_HOST_ID;
    const shadow = this.host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS_TEXT;
    this.root = div('root');
    this.root.append(this.pendingBox, this.hoverBox, this.hoverLabel, this.dragBox, this.hint);
    for (const n of [this.hoverBox, this.hoverLabel, this.dragBox, this.pendingBox, this.hint]) n.style.display = 'none';
    this.hint.dataset.role = 'hint';
    this.hint.innerHTML = Overlay.HINT_HTML;
    this.hoverBox.dataset.role = 'hover';
    this.hoverLabel.dataset.role = 'hover-label';
    this.dragBox.dataset.role = 'drag';
    this.pendingBox.dataset.role = 'pending';
    shadow.append(style, this.root);
  }

  /** Attach (or re-attach, if the page removed it) to <html>. */
  mount(): void {
    // A newer picker instance took over this page: never re-attach the stale overlay.
    if (!this.isActive()) {
      this.host.remove();
      return;
    }
    const parent = document.documentElement;
    if (parent && this.host.parentNode !== parent) parent.appendChild(this.host);
  }

  isOurs(node: Node | null): boolean {
    return node === this.host;
  }

  setVisible(v: boolean): void {
    this.visible = v;
    this.host.style.display = v ? '' : 'none';
    if (v) this.mount();
  }
  get isVisible(): boolean {
    return this.visible;
  }

  showHover(rect: Rect | null, label?: string): void {
    this.mount();
    if (!rect) {
      this.hoverBox.style.display = 'none';
      this.hoverLabel.style.display = 'none';
      return;
    }
    this.hoverBox.style.display = '';
    place(this.hoverBox, rect);
    if (label !== undefined) this.hoverLabel.textContent = label;
    this.hoverLabel.style.display = '';
    const at = labelPosition(rect, { w: window.innerWidth, h: window.innerHeight });
    this.hoverLabel.style.transform = `translate(${at.x}px, ${at.y}px)`;
  }

  /** The "⌥ is held" hint bar: tells the user the picker is alive even over nothing. */
  showHint(on: boolean): void {
    if (this.hintTimer) return; // a flashed message is showing
    if (on) this.mount();
    this.hint.style.display = on ? '' : 'none';
  }

  /** Show a message in the hint bar for a few seconds (e.g. a pick nobody answered). */
  flashHint(text: string, ms = 6000): void {
    this.mount();
    if (this.hintTimer) clearTimeout(this.hintTimer);
    this.hint.textContent = text;
    this.hint.style.display = '';
    this.hintTimer = setTimeout(() => {
      this.hintTimer = null;
      this.hint.innerHTML = Overlay.HINT_HTML;
      this.hint.style.display = 'none';
    }, ms);
  }

  setHoverLabel(label: string): void {
    this.hoverLabel.textContent = label;
  }

  showDrag(rect: Rect | null): void {
    if (!rect) {
      this.dragBox.style.display = 'none';
      return;
    }
    this.mount();
    this.dragBox.style.display = '';
    place(this.dragBox, rect);
  }

  /** Outline of the just-picked target until the worker accepts it and the pin takes over. */
  showPending(rect: Rect | null): void {
    if (!rect) {
      this.pendingBox.style.display = 'none';
      return;
    }
    this.mount();
    this.pendingBox.style.display = '';
    place(this.pendingBox, rect);
  }

  /** Create/update/remove pin nodes to match `pins`. */
  renderPins(pins: PinView[]): void {
    const live = new Set<number>();
    for (const p of pins) {
      live.add(p.id);
      let n = this.pinNodes.get(p.id);
      if (!n) {
        const wrap = div('pin');
        const box = div('box pin-box');
        const badge = div('badge');
        badge.textContent = String(p.id);
        wrap.append(box, badge);
        wrap.dataset.pinId = String(p.id);
        this.root.append(wrap);
        n = { wrap, box, badge };
        this.pinNodes.set(p.id, n);
      }
      n.wrap.dataset.state = p.state;
      if (!p.rect) {
        n.wrap.style.display = 'none';
        continue;
      }
      n.wrap.style.display = '';
      n.wrap.style.transform = '';
      place(n.box, p.rect);
      // Badge: anchored to the top-left corner, nudged to stay on screen.
      n.badge.style.transform = `translate(${Math.max(2, p.rect.x - 9)}px, ${Math.max(2, p.rect.y - 9)}px)`;
      n.wrap.dataset.x = String(Math.round(p.rect.x));
      n.wrap.dataset.y = String(Math.round(p.rect.y));
    }
    for (const [id, n] of this.pinNodes) {
      if (!live.has(id)) {
        n.wrap.remove();
        this.pinNodes.delete(id);
      }
    }
  }
}
