// ⌥ input handling (SPEC §5.2). Window-level capture-phase listeners, intercepting only while
// ⌥ is held (or while one of our own alt-drags is in flight), so page clicks are untouched.
import type { Rect } from '../protocol.gen';
import type { Overlay } from './overlay';
import { currentOf, moveDown, moveUp, originOf, startPath, type PathStack } from './traversal';
import { guard, isDrag, rectFromPoints, rectOf } from './util';

export type PickTarget = { kind: 'element'; el: Element } | { kind: 'region'; rect: Rect };

export interface InputDeps {
  /** False once a newer picker instance has taken over the page. */
  isActive?(): boolean;
  overlay: Overlay;
  onPick(target: PickTarget): void;
  onEscape(): void;
  /** True when Esc has something to clear (live pins). */
  hasLivePins(): boolean;
  /** Label for a hovered element: "Card  src/lib/Card.svelte:8". Null when unknown. */
  labelFor(el: Element): Promise<string | null>;
  /** Called after the hover target moved (so main can refresh layout). */
  onHoverChange?(el: Element | null): void;
}

/** A wrapper this large (of the viewport) is skipped when something smaller sits under the cursor. */
const HUGE_FRACTION = 0.85;

/**
 * Which element to hover from the stack under the cursor (topmost first). Page-sized wrappers
 * (app shells, full-screen containers) would highlight the whole page, which looks like nothing
 * happened, so prefer the first element that isn't one. Falls back to the topmost.
 */
export function chooseTarget<T>(stack: readonly T[], areaOf: (el: T) => number, viewportArea: number): T | null {
  const first = stack[0];
  if (first === undefined) return null;
  const limit = viewportArea * HUGE_FRACTION;
  for (const el of stack) if (areaOf(el) < limit) return el;
  return first;
}

const INTERCEPTED = ['pointerdown', 'pointerup', 'mouseup', 'click', 'dblclick', 'auxclick'] as const;

/** Tag.class fallback label. */
export function fallbackLabel(el: Element): string {
  const cls = Array.from(el.classList).slice(0, 2);
  return `${el.tagName.toLowerCase()}${cls.map((c) => `.${c}`).join('')}`;
}

export const parentOfElement = (el: Element): Element | null => {
  const p = el.parentElement;
  return p && p !== document.documentElement ? p : null;
};

export class InputController {
  private altDown = false;
  private last: { x: number; y: number } | null = null;
  private path: PathStack<Element> | null = null;
  private shown: Element | null = null;
  private labelToken = 0;
  private drag: { x0: number; y0: number; target: Element | null; active: boolean } | null = null;
  private swallowClickUntil = 0;

  constructor(private readonly deps: InputDeps) {}

  install(win: Window = window): void {
    const opts = { capture: true } as const;
    const live = <E extends Event>(fn: (e: E) => void) => (e: E) => {
      if (this.deps.isActive && !this.deps.isActive()) return;
      fn(e);
    };
    win.addEventListener('keydown', guard('keydown', live((e: KeyboardEvent) => this.onKeyDown(e))), opts);
    win.addEventListener('keyup', guard('keyup', live((e: KeyboardEvent) => this.onKeyUp(e))), opts);
    win.addEventListener('blur', guard('blur', () => this.reset()), opts);
    document.addEventListener('visibilitychange', guard('visibility', () => this.reset()), opts);
    win.addEventListener('mousemove', guard('mousemove', live((e: MouseEvent) => this.onMouseMove(e))), { capture: true, passive: true });
    win.addEventListener('mousedown', guard('mousedown', live((e: MouseEvent) => this.onMouseDown(e))), opts);
    win.addEventListener('mouseup', guard('mouseup', live((e: MouseEvent) => this.onMouseUp(e))), opts);
    for (const type of INTERCEPTED) {
      if (type === 'mouseup') continue;
      win.addEventListener(type, guard(type, live((e: Event) => this.onSwallow(e as MouseEvent))), opts);
    }
    for (const type of ['dragstart', 'selectstart'] as const) {
      win.addEventListener(
        type,
        guard(
          type,
          live((e: Event) => {
            if (this.drag || this.altDown) e.preventDefault();
          }),
        ),
        opts,
      );
    }
  }

  /** The element the next pick would use (honouring ↑/↓ traversal), for tests/debug. */
  get hoverTarget(): Element | null {
    return this.shown;
  }

  private reset(): void {
    this.setAlt(false);
    this.drag = null;
    this.deps.overlay.showDrag(null);
    this.clearHover();
  }

  private onKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Alt') {
      this.setAlt(true);
      if (this.last) this.hoverAt(this.last.x, this.last.y);
      return;
    }
    if ((e.key === 'ArrowUp' || e.key === 'ArrowDown') && (e.altKey || this.altDown) && this.path && this.shown) {
      e.preventDefault();
      e.stopImmediatePropagation();
      this.path = e.key === 'ArrowUp' ? moveUp(this.path, parentOfElement) : moveDown(this.path);
      this.show(currentOf(this.path));
      return;
    }
    if (e.key === 'Escape' && (this.shown || this.deps.hasLivePins())) {
      this.deps.onEscape();
      this.clearHover();
    }
  }

  private onKeyUp(e: KeyboardEvent): void {
    if (e.key !== 'Alt') return;
    this.setAlt(false);
    if (!this.drag) this.clearHover();
  }

  private onMouseMove(e: MouseEvent): void {
    this.last = { x: e.clientX, y: e.clientY };
    if (this.drag) {
      const d = this.drag;
      if (!d.active && isDrag(e.clientX - d.x0, e.clientY - d.y0)) {
        d.active = true;
        this.clearHover();
      }
      if (d.active) this.deps.overlay.showDrag(rectFromPoints(d.x0, d.y0, e.clientX, e.clientY));
      return;
    }
    if (e.altKey) {
      if (!this.altDown) this.setAlt(true);
      this.hoverAt(e.clientX, e.clientY);
    } else if (this.altDown || this.shown) {
      this.setAlt(false);
      this.clearHover();
    }
  }

  private elementAt(x: number, y: number): Element | null {
    const stack = document.elementsFromPoint(x, y).filter((e) => !this.deps.overlay.isOurs(e) && e !== document.documentElement);
    const area = (el: Element): number => {
      const r = el.getBoundingClientRect();
      return r.width * r.height;
    };
    return chooseTarget(stack, area, window.innerWidth * window.innerHeight);
  }

  private setAlt(down: boolean): void {
    this.altDown = down;
    this.deps.overlay.showHint(down);
  }

  private hoverAt(x: number, y: number): void {
    const el = this.elementAt(x, y);
    if (!el) return;
    if (!this.path || originOf(this.path) !== el) this.path = startPath(el);
    const target = currentOf(this.path);
    if (target !== this.shown) this.show(target);
  }

  private show(el: Element): void {
    this.shown = el;
    this.deps.overlay.showHover(rectOf(el.getBoundingClientRect()), fallbackLabel(el));
    this.deps.onHoverChange?.(el);
    const token = ++this.labelToken;
    this.deps
      .labelFor(el)
      .then((label) => {
        if (label && token === this.labelToken && this.shown === el) this.deps.overlay.setHoverLabel(label);
      })
      .catch(() => {});
  }

  private clearHover(): void {
    this.labelToken++;
    this.shown = null;
    this.path = null;
    this.deps.overlay.showHover(null);
    this.deps.onHoverChange?.(null);
  }

  /** Re-measure the hover box (the page scrolled or changed). */
  refreshHover(): void {
    if (this.shown) {
      if (!this.shown.isConnected) this.clearHover();
      else this.deps.overlay.showHover(rectOf(this.shown.getBoundingClientRect()));
    }
  }

  private onMouseDown(e: MouseEvent): void {
    if (!e.altKey || e.button !== 0) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    this.setAlt(true);
    const under = this.elementAt(e.clientX, e.clientY);
    // ↑/↓ may have moved the selection off the element under the pointer.
    const target = this.path && under && originOf(this.path) === under ? currentOf(this.path) : under;
    this.drag = { x0: e.clientX, y0: e.clientY, target, active: false };
  }

  private onMouseUp(e: MouseEvent): void {
    const d = this.drag;
    if (!d) {
      if (e.altKey) this.onSwallow(e);
      return;
    }
    e.preventDefault();
    e.stopImmediatePropagation();
    this.drag = null;
    this.swallowClickUntil = Date.now() + 100;
    this.deps.overlay.showDrag(null);
    if (d.active || isDrag(e.clientX - d.x0, e.clientY - d.y0)) {
      const rect = rectFromPoints(d.x0, d.y0, e.clientX, e.clientY);
      this.clearHover();
      this.deps.onPick({ kind: 'region', rect });
    } else if (d.target) {
      this.clearHover();
      this.deps.onPick({ kind: 'element', el: d.target });
    }
  }

  /** Alt-modified clicks (and the click that follows our alt press) must not reach the page. */
  private onSwallow(e: MouseEvent): void {
    if (e.altKey || (e.type === 'click' && Date.now() < this.swallowClickUntil)) {
      if (e.type !== 'pointerdown' && e.type !== 'pointerup') e.preventDefault();
      e.stopImmediatePropagation();
    }
  }
}
