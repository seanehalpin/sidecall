// Page-side entry (MAIN world, document_start): wires input, overlay, pins and the channel.
import type { ExtToPage, PageToExt, PinSpec } from '../picker-protocol';
import type { Rect } from '../protocol.gen';
import { installConsoleCapture } from './console-capture';
import { createPageChannel, type PageChannel } from './channel';
import { domEnv } from './dom-env';
import { resolveFramework } from './frameworks';
import { installHmrTracking } from './frameworks/hmr';
import { installDevtoolsShim } from './frameworks/rdt-shim';
import { InputController, type PickTarget } from './input';
import { Overlay } from './overlay';
import { buildElementPick, buildRegionPick, reanchorFor } from './payload';
import { PinRegistry } from './pins';
import { displayPath, guard, warnOnce } from './util';

const BOOT_KEY = Symbol.for('sidecall.picker.booted');
const PENDING_TTL_MS = 5000;
const OVERLAY_ACK_FALLBACK_MS = 400;

declare global {
  interface Window {
    [BOOT_KEY]?: boolean;
    /** Test/debug handle. */
    __sidecallPicker?: { pins: PinRegistry; input: InputController; overlay: Overlay };
  }
}

interface PendingPick {
  el: Element | null;
  regionDoc: Rect | null;
  timer: ReturnType<typeof setTimeout>;
}

export function bootPicker(): void {
  if (window[BOOT_KEY]) return;
  window[BOOT_KEY] = true;
  try {
    run();
  } catch (err) {
    warnOnce('boot', err);
  }
}

function run(): void {
  installConsoleCapture();
  installDevtoolsShim();
  installHmrTracking();

  const overlay = new Overlay();
  const pending = new Map<string, PendingPick>();
  let seq = 0;
  let channel: PageChannel;
  const post = (m: PageToExt) => channel.send(m);

  const pins = new PinRegistry({
    overlay,
    post,
    env: domEnv,
    refreshAnchor: (el, prev) => reanchorFor(el, prev),
  });

  const input = new InputController({
    overlay,
    onPick: (t) => void handlePick(t),
    onEscape: () => post({ t: 'escape' }),
    hasLivePins: () => pins.hasLive,
    labelFor: async (el) => {
      const r = await resolveFramework(el, { full: false });
      const s = r.framework.source;
      const where = s ? `${displayPath(s.file)}:${s.line}` : null;
      if (!r.componentName && !where) return null;
      return [r.componentName, where].filter(Boolean).join('  ');
    },
    onHoverChange: () => {},
  });

  async function handlePick(t: PickTarget): Promise<void> {
    const localId = `p${Date.now().toString(36)}-${++seq}`;
    const built = t.kind === 'element' ? await buildElementPick(t.el) : await buildRegionPick(t.rect);
    const regionDoc = built.anchor.region;
    overlay.showPending(built.rect);
    const timer = setTimeout(() => {
      pending.delete(localId);
      overlay.showPending(null);
    }, PENDING_TTL_MS);
    pending.set(localId, { el: t.kind === 'element' ? t.el : null, regionDoc, timer });
    post({ t: 'pick', localId, draft: built.draft, anchor: built.anchor, rect: built.rect, viewport: built.viewport });
  }

  function onMessage(m: ExtToPage): void {
    switch (m.t) {
      case 'pin-add': {
        const p = pending.get(m.localId);
        if (p) {
          clearTimeout(p.timer);
          pending.delete(m.localId);
          overlay.showPending(null);
        }
        pins.addLocal(m.pin as PinSpec, p?.el ?? null, p?.regionDoc ?? null);
        break;
      }
      case 'pin-remove':
        pins.remove(m.id);
        break;
      case 'pins-clear':
        pins.clear();
        break;
      case 'pins-restore':
        pins.restore(m.pins);
        break;
      case 'pin-dim':
        pins.dim(m.ids, m.dimmed);
        break;
      case 'pin-highlight':
        pins.highlight(m.id);
        break;
      case 'overlay-set': {
        overlay.setVisible(m.visible);
        // Ack once the change is applied AND a frame has painted, so a screenshot is clean.
        let done = false;
        const ack = () => {
          if (done) return;
          done = true;
          post({ t: 'overlay-ack', reqId: m.reqId, visible: m.visible });
        };
        requestAnimationFrame(() => requestAnimationFrame(ack));
        setTimeout(ack, OVERLAY_ACK_FALLBACK_MS);
        break;
      }
    }
  }

  channel = createPageChannel(guard('message', onMessage));
  input.install();
  window.__sidecallPicker = { pins, input, overlay };

  // Layout: pins and hover follow scroll/resize/DOM change (coalesced to one rAF).
  const relayout = guard('layout', () => {
    pins.requestLayout();
    input.refreshHover();
  });
  window.addEventListener('scroll', relayout, { capture: true, passive: true });
  window.addEventListener('resize', relayout, { passive: true });

  const startObserving = () => {
    overlay.mount();
    if (!document.documentElement) return;
    const mo = new MutationObserver(
      guard('mutation', (records: MutationRecord[]) => {
        if (records.every((r) => overlay.isOurs(r.target) || [...r.addedNodes, ...r.removedNodes].every((n) => overlay.isOurs(n)))) return;
        pins.requestLayout();
        pins.scheduleReanchor();
      }),
    );
    mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  };

  const announce = () => {
    startObserving();
    channel.handshake();
    post({ t: 'ready', url: location.href });
  };
  // 'ready' waits until parsing started so the bridge (also document_start) is surely listening.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', guard('ready', announce), { once: true });
  else announce();
}
