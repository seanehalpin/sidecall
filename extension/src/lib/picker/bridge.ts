// Isolated-world bridge: page (window.postMessage + nonce) <-> service worker (chrome.runtime).
import {
  PICKER_CHANNEL,
  PICKER_RUNTIME_KIND,
  type ExtToPage,
  type PageEnvelope,
  type PageToExt,
  type PickerRuntimeUp,
} from '../picker-protocol';

export interface BridgeDeps {
  /** The page's window (events from any other source are ignored). */
  win: Window;
  /** chrome.runtime.sendMessage, wrapped so a dead extension context is just a rejection. */
  sendUp(m: PickerRuntimeUp): Promise<unknown> | void;
}

export class Bridge {
  private nonce: string | null = null;
  constructor(private readonly deps: BridgeDeps) {}

  /** window 'message' listener: handshake (first one wins) and page → worker relay. */
  onWindowMessage = (e: { source: unknown; data: unknown }): void => {
    if (e.source !== this.deps.win) return;
    const d = e.data as { channel?: string; dir?: string; nonce?: unknown; msg?: unknown } | null;
    if (!d || d.channel !== PICKER_CHANNEL || typeof d.nonce !== 'string') return;
    if (d.dir === 'handshake') {
      if (this.nonce === null) this.nonce = d.nonce;
      return;
    }
    if (d.dir !== 'page-to-ext') return;
    // A bridge that started after the handshake adopts the nonce of the page's first message.
    if (this.nonce === null) this.nonce = d.nonce;
    if (d.nonce !== this.nonce) return;
    try {
      const res = this.deps.sendUp({ kind: PICKER_RUNTIME_KIND, msg: d.msg as PageToExt });
      if (res && typeof (res as Promise<unknown>).catch === 'function') (res as Promise<unknown>).catch(() => {});
    } catch {
      /* extension reloaded: nothing to deliver to */
    }
  };

  /** chrome.runtime.onMessage listener: worker → page. */
  onRuntimeMessage = (m: unknown): void => {
    const d = m as { kind?: string; msg?: ExtToPage } | null;
    if (!d || d.kind !== PICKER_RUNTIME_KIND || !d.msg || this.nonce === null) return;
    const env: PageEnvelope = { channel: PICKER_CHANNEL, nonce: this.nonce, dir: 'ext-to-page', msg: d.msg };
    this.deps.win.postMessage(env, '*');
  };
}
