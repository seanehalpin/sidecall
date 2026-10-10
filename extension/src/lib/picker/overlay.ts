// The picker's visuals: one shadow-DOM root on <html> at the highest z-index. Everything in
// it is pointer-events: none; input is captured with window-level listeners instead, only
// while ⌥ is held, so normal page interaction is never affected.
import type { Rect } from '../protocol.gen';
import { OVERLAY_HOST_ID } from './util';

export type PinState = 'normal' | 'dimmed' | 'highlighted' | 'stale';

export interface PinView {
  id: number;
  /** Viewport rect, or null when there is nothing to draw (yet). */
  rect: Rect | null;
  state: PinState;
}

const CSS_TEXT = `
:host { all: initial; }
.root { position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 2147483647;
  font: 11px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace; }
.box { position: absolute; box-sizing: border-box; border-radius: 2px; }
.hover { border: 1.5px solid #6d5efc; background: rgba(109, 94, 252, 0.12); }
.drag { border: 1.5px dashed #6d5efc; background: rgba(109, 94, 252, 0.10); }
.pending { border: 1.5px solid #6d5efc; background: rgba(109, 94, 252, 0.08); }
.label { position: absolute; max-width: 60vw; padding: 2px 6px; border-radius: 3px; background: #1d1b2f;
  color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pin-box { border: 1.5px solid #6d5efc; }
.badge { position: absolute; top: 0; left: 0; min-width: 18px; height: 18px; padding: 0 4px; box-sizing: border-box;
  border-radius: 9px; background: #6d5efc; color: #fff; font-weight: 700; text-align: center; line-height: 18px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, .35); }
[data-state="dimmed"] .pin-box { border-color: rgba(109, 94, 252, .35); }
[data-state="dimmed"] .badge { background: rgba(109, 94, 252, .55); }
[data-state="dimmed"] { opacity: .75; }
[data-state="highlighted"] .pin-box { border-width: 3px; background: rgba(109, 94, 252, .18); }
[data-state="highlighted"] .badge { background: #ff5c8a; transform: scale(1.2); }
[data-state="stale"] .pin-box { border: 1.5px dashed #8a8a96; }
[data-state="stale"] .badge { background: #8a8a96; }
.pin { position: absolute; top: 0; left: 0; }
`;

function div(cls: string): HTMLDivElement {
  const d = document.createElement('div');
  d.className = cls;
  return d;
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
  private readonly pinNodes = new Map<number, { wrap: HTMLDivElement; box: HTMLDivElement; badge: HTMLDivElement }>();
  private visible = true;

  constructor() {
    this.host = document.createElement('div');
    this.host.id = OVERLAY_HOST_ID;
    const shadow = this.host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = CSS_TEXT;
    this.root = div('root');
    this.root.append(this.pendingBox, this.hoverBox, this.hoverLabel, this.dragBox);
    for (const n of [this.hoverBox, this.hoverLabel, this.dragBox, this.pendingBox]) n.style.display = 'none';
    this.hoverBox.dataset.role = 'hover';
    this.hoverLabel.dataset.role = 'hover-label';
    this.dragBox.dataset.role = 'drag';
    this.pendingBox.dataset.role = 'pending';
    shadow.append(style, this.root);
  }

  /** Attach (or re-attach, if the page removed it) to <html>. */
  mount(): void {
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
    const below = rect.y < 24;
    this.hoverLabel.style.transform = `translate(${Math.max(0, rect.x)}px, ${below ? rect.y + rect.h + 4 : rect.y - 22}px)`;
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
