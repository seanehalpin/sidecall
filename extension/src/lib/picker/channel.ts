// Page side of the page <-> bridge window.postMessage channel (SPEC §5.2).
import {
  PICKER_CHANNEL,
  type ExtToPage,
  type PageEnvelope,
  type PageHandshake,
  type PageToExt,
} from '../picker-protocol';

export function makeNonce(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  }
}

export interface PageChannel {
  nonce: string;
  send(msg: PageToExt): void;
  /** Announce the nonce again (the bridge may have started after the first announcement). */
  handshake(): void;
}

export function createPageChannel(onMessage: (m: ExtToPage) => void, win: Window = window): PageChannel {
  const nonce = makeNonce();
  const handshake = () => {
    const h: PageHandshake = { channel: PICKER_CHANNEL, dir: 'handshake', nonce };
    win.postMessage(h, '*');
  };
  win.addEventListener('message', (e: MessageEvent) => {
    if (e.source !== win) return;
    const d = e.data as Partial<PageEnvelope> | null;
    if (!d || d.channel !== PICKER_CHANNEL || d.dir !== 'ext-to-page' || d.nonce !== nonce) return;
    try {
      onMessage(d.msg as ExtToPage);
    } catch (err) {
      console.warn('[sidecall] picker message handler failed', err);
    }
  });
  handshake();
  return {
    nonce,
    handshake,
    send(msg) {
      const env: PageEnvelope = { channel: PICKER_CHANNEL, nonce, dir: 'page-to-ext', msg };
      win.postMessage(env, '*');
    },
  };
}
