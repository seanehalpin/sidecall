// Page-side view of the worker's RailState (side panel and options page).
// Renders the last persisted state from storage.session immediately, then
// follows the live state pushed over a runtime port.
import { browser, type Browser } from 'wxt/browser';
import {
  DEFAULT_DAEMON_PORT,
  STORAGE_KEYS,
  type PageCommand,
  type PageDraft,
  type WorkerMessage,
} from './messaging';
import { initialRailState, type RailState } from './rail-state';

export interface RailClient {
  readonly state: RailState;
  readonly daemonPort: number;
  /** True once live state has arrived from the worker. */
  readonly live: boolean;
  command(cmd: PageCommand): void;
  send(draft: PageDraft): void;
}

export function createRailClient(portName: string): RailClient {
  // Replaced wholesale on every update, so no deep proxy is needed.
  let state = $state.raw<RailState>(initialRailState());
  let daemonPort = $state(DEFAULT_DAEMON_PORT);
  let live = $state(false);
  let port: Browser.runtime.Port | null = null;
  const pending: PageCommand[] = [];

  void browser.storage.session.get(STORAGE_KEYS.railState).then((stored) => {
    const saved = stored[STORAGE_KEYS.railState] as Partial<RailState> | undefined;
    if (!live && saved) state = { ...initialRailState(), ...saved, connection: 'connecting' };
  });

  function connect(): void {
    const p = browser.runtime.connect({ name: portName });
    port = p;
    p.onMessage.addListener((m: unknown) => {
      const msg = m as WorkerMessage;
      if (msg.kind !== 'state') return;
      state = msg.state;
      daemonPort = msg.daemonPort;
      live = true;
    });
    p.onDisconnect.addListener(() => {
      // The worker was stopped (e.g. idle while the daemon is down). Reconnecting
      // wakes it; a short delay avoids a tight loop.
      if (port === p) port = null;
      setTimeout(connect, 1000);
    });
    while (pending.length) p.postMessage(pending.shift());
  }
  connect();

  function command(cmd: PageCommand): void {
    if (port) {
      try {
        port.postMessage(cmd);
        return;
      } catch {
        port = null;
      }
    }
    pending.push(cmd);
  }

  return {
    get state() {
      return state;
    },
    get daemonPort() {
      return daemonPort;
    },
    get live() {
      return live;
    },
    command,
    send: (draft) => command({ cmd: 'send', draft }),
  };
}
