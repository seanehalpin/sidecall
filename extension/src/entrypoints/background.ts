// Service worker: the single owner of the daemon WebSocket (SPEC §5.6, §9.1).
//
// - Connects to ws://127.0.0.1:<port>/ws, answers every JSON ping with a pong
//   (only data messages keep an MV3 worker alive), reconnects with backoff and
//   is woken every 30 s by an alarm when the daemon is down.
// - Pairs (pair.request → pair.result), then authenticates with hello. Sends
//   hello again on the same socket whenever the active tab's origin changes.
// - Keeps RailState via the pure reducer, persists it to storage.session and
//   pushes it to every connected page.
import { browser, type Browser } from 'wxt/browser';
import { backoffDelay } from '@/lib/backoff';
import {
  DEFAULT_DAEMON_PORT,
  OPTIONS_PORT,
  RAIL_PORT,
  STORAGE_KEYS,
  isPageDraft,
  normalisePairingCode,
  parsePort,
  type PageCommand,
  type WorkerMessage,
} from '@/lib/messaging';
import { allowedOrigin } from '@/lib/origin';
import {
  envelope,
  envelopeFromDraft,
  parseServerMessage,
  type ClientMessage,
  type NotifyPayload,
  type ServerMessage,
} from '@/lib/protocol';
import {
  applyLocalEvent,
  applyServerMessage,
  initialRailState,
  type LocalEvent,
  type RailState,
} from '@/lib/rail-state';

const WAKE_ALARM = 'sidecall-wake';
const NOTIFICATION_PREFIX = 'sidecall';
const PERSIST_DELAY_MS = 150;

export default defineBackground(() => {
  const extVersion = browser.runtime.getManifest().version;
  const ownPagePrefix = browser.runtime.getURL('/');

  let state: RailState = initialRailState();
  let daemonPort = DEFAULT_DAEMON_PORT;
  let token: string | null = null;
  let ws: WebSocket | null = null;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let persistTimer: ReturnType<typeof setTimeout> | null = null;
  let lastWindowId: number | null = null;
  const pages = new Map<Browser.runtime.Port, string>();

  // --- State -----------------------------------------------------------------

  function setState(next: RailState): void {
    if (next === state) return;
    state = next;
    broadcast();
    schedulePersist();
  }

  const local = (ev: LocalEvent) => setState(applyLocalEvent(state, ev));

  function schedulePersist(): void {
    if (persistTimer) return;
    persistTimer = setTimeout(() => {
      persistTimer = null;
      void browser.storage.session.set({ [STORAGE_KEYS.railState]: state });
    }, PERSIST_DELAY_MS);
  }

  function post(port: Browser.runtime.Port): void {
    const msg: WorkerMessage = { kind: 'state', state, daemonPort };
    try {
      port.postMessage(msg);
    } catch {
      pages.delete(port);
    }
  }

  function broadcast(): void {
    for (const port of pages.keys()) post(port);
  }

  const railOpen = () => [...pages.values()].includes(RAIL_PORT);

  // --- Socket ----------------------------------------------------------------

  const isOpen = () => ws !== null && ws.readyState === WebSocket.OPEN;

  function send(msg: ClientMessage): boolean {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(msg));
    return true;
  }

  function sendHello(): void {
    if (!token || !isOpen()) return;
    send(envelope('hello', { token, extVersion, origin: state.origin, clockSample: Date.now() }));
  }

  function clearReconnect(): void {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  function scheduleReconnect(): void {
    clearReconnect();
    const delay = backoffDelay(attempt);
    attempt += 1;
    reconnectTimer = setTimeout(connect, delay);
  }

  function connect(): void {
    clearReconnect();
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
    local({ kind: 'connection', status: 'connecting' });
    let sock: WebSocket;
    try {
      sock = new WebSocket(`ws://127.0.0.1:${daemonPort}/ws`);
    } catch {
      local({ kind: 'connection', status: 'offline' });
      scheduleReconnect();
      return;
    }
    ws = sock;
    sock.onopen = () => {
      if (ws !== sock) return;
      attempt = 0;
      local({ kind: 'connection', status: 'connected' });
      sendHello();
    };
    sock.onmessage = (ev: MessageEvent) => {
      if (ws !== sock) return;
      const msg = parseServerMessage(ev.data);
      if (msg) void handleServer(msg);
    };
    sock.onclose = (ev: CloseEvent) => {
      if (ws !== sock) return;
      ws = null;
      if (ev.code === 4401) void forgetToken();
      local({ kind: 'connection', status: 'offline' });
      scheduleReconnect();
    };
  }

  /** Drop the current socket (if any) and connect again straight away. */
  function reconnectNow(): void {
    attempt = 0;
    const old = ws;
    ws = null;
    if (old) old.close(1000, 'reconnect');
    connect();
  }

  async function forgetToken(): Promise<void> {
    token = null;
    await browser.storage.local.remove(STORAGE_KEYS.token);
    local({ kind: 'paired', paired: false });
  }

  async function handleServer(msg: ServerMessage): Promise<void> {
    switch (msg.type) {
      case 'ping':
        send(envelope('pong', { seq: msg.payload.seq }));
        return;
      case 'pair.result':
        token = msg.payload.token;
        setState(applyServerMessage(state, msg));
        await browser.storage.local.set({ [STORAGE_KEYS.token]: token });
        sendHello();
        return;
      case 'error':
        if (msg.payload.code === 'unauthorized') {
          token = null;
          void browser.storage.local.remove(STORAGE_KEYS.token);
        }
        break;
      case 'notify':
        showNotification(msg.payload);
        break;
    }
    setState(applyServerMessage(state, msg));
  }

  // --- Active tab origin (SPEC §9.1) -----------------------------------------

  function rememberWindow(windowId: number): void {
    if (windowId === lastWindowId || windowId < 0) return;
    lastWindowId = windowId;
    void browser.storage.session.set({ [STORAGE_KEYS.lastWindowId]: windowId });
  }

  async function refreshOrigin(): Promise<void> {
    let tab: Browser.tabs.Tab | undefined;
    try {
      [tab] = await browser.tabs.query({ active: true, lastFocusedWindow: true });
    } catch {
      return;
    }
    if (!tab) return;
    if (tab.windowId !== undefined) rememberWindow(tab.windowId);
    // Our own pages (options, a panel opened as a tab) keep the current binding.
    if (tab.url?.startsWith(ownPagePrefix)) return;
    const origin = allowedOrigin(tab.url);
    if (origin === state.origin) return;
    local({ kind: 'origin', origin });
    sendHello();
  }

  // --- Notifications ---------------------------------------------------------

  function showNotification(p: NotifyPayload): void {
    if (railOpen()) return;
    // The window id rides in the notification id, so the click handler can call
    // sidePanel.open synchronously, inside the user gesture, even after a restart.
    const id = `${NOTIFICATION_PREFIX}:${lastWindowId ?? -1}:${Date.now()}`;
    void browser.notifications
      .create(id, {
        type: 'basic',
        iconUrl: browser.runtime.getURL('/icon/128.png'),
        title: p.title,
        message: p.body,
        priority: 1,
      })
      .catch((err: unknown) => console.warn('[sidecall] notification failed', err));
  }

  function focusWindow(windowId: number | null): void {
    const target = windowId !== null && windowId >= 0 ? Promise.resolve(windowId) : browser.windows.getLastFocused().then((w) => w.id);
    void target
      .then((id) => (id === undefined ? undefined : browser.windows.update(id, { focused: true })))
      .catch(() => {});
  }

  browser.notifications.onClicked.addListener((notificationId) => {
    const [prefix, win] = notificationId.split(':');
    if (prefix !== NOTIFICATION_PREFIX) return;
    const windowId = Number(win);
    void browser.notifications.clear(notificationId);
    if (Number.isInteger(windowId) && windowId >= 0) {
      // Must run before any await to stay inside the click's user gesture.
      browser.sidePanel
        .open({ windowId })
        .then(() => focusWindow(windowId))
        .catch((err: unknown) => {
          console.warn('[sidecall] sidePanel.open from notification failed', err);
          focusWindow(windowId);
        });
    } else {
      focusWindow(null);
    }
  });

  // --- Pages (rail, options) -------------------------------------------------

  async function handleCommand(cmd: PageCommand): Promise<void> {
    switch (cmd.cmd) {
      case 'send':
        if (!isPageDraft(cmd.draft)) return;
        if (!token || !send(envelopeFromDraft(cmd.draft))) {
          local({ kind: 'error', error: { code: 'not_connected', message: 'Not connected to the daemon.' } });
        }
        return;
      case 'pair': {
        const code = normalisePairingCode(cmd.code);
        if (!code) {
          local({ kind: 'error', error: { code: 'bad_pairing_code', message: 'Enter the 6-digit code.' } });
          return;
        }
        local({ kind: 'clearError' });
        if (!send(envelope('pair.request', { code }))) {
          local({ kind: 'error', error: { code: 'not_connected', message: 'The daemon is not running.' } });
        }
        return;
      }
      case 'forgetPairing':
        await forgetToken();
        reconnectNow();
        return;
      case 'setPort': {
        const port = parsePort(cmd.port);
        if (port === null) return;
        // storage.onChanged picks this up and reconnects.
        await browser.storage.local.set({ [STORAGE_KEYS.port]: port });
        return;
      }
      case 'reconnect':
        reconnectNow();
        return;
      case 'clearError':
        local({ kind: 'clearError' });
        return;
      case 'openOptions':
        await browser.runtime.openOptionsPage();
        return;
    }
  }

  browser.runtime.onConnect.addListener((port) => {
    if (port.name !== RAIL_PORT && port.name !== OPTIONS_PORT) return;
    if (!port.sender?.url?.startsWith(ownPagePrefix)) {
      port.disconnect();
      return;
    }
    pages.set(port, port.name);
    port.onMessage.addListener((m: unknown) => {
      void ready.then(() => handleCommand(m as PageCommand));
    });
    port.onDisconnect.addListener(() => pages.delete(port));
    void ready.then(() => post(port));
  });

  // --- Storage changes (port, token edited elsewhere) ------------------------

  browser.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    void ready.then(() => {
      const portChange = changes[STORAGE_KEYS.port];
      if (portChange) {
        const next = parsePort(portChange.newValue) ?? DEFAULT_DAEMON_PORT;
        if (next !== daemonPort) {
          daemonPort = next;
          broadcast();
          reconnectNow();
        }
      }
      const tokenChange = changes[STORAGE_KEYS.token];
      if (tokenChange) {
        const next = typeof tokenChange.newValue === 'string' && tokenChange.newValue ? tokenChange.newValue : null;
        if (next === token) return;
        token = next;
        local({ kind: 'paired', paired: token !== null });
        if (token) sendHello();
        else reconnectNow();
      }
    });
  });

  // --- Tab and window tracking -----------------------------------------------

  browser.tabs.onActivated.addListener((info) => {
    rememberWindow(info.windowId);
    void ready.then(refreshOrigin);
  });
  browser.tabs.onUpdated.addListener((_tabId, change, tab) => {
    if (!tab.active || (change.url === undefined && change.status === undefined)) return;
    void ready.then(refreshOrigin);
  });
  browser.windows.onFocusChanged.addListener((windowId) => {
    if (windowId === browser.windows.WINDOW_ID_NONE) return;
    rememberWindow(windowId);
    void ready.then(refreshOrigin);
  });

  // --- Wake and startup ------------------------------------------------------

  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name !== WAKE_ALARM) return;
    void ready.then(() => {
      if (!ws) connect();
    });
  });

  browser.runtime.onInstalled.addListener(() => {
    void browser.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  });

  const ready = (async () => {
    void browser.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
    if (!(await browser.alarms.get(WAKE_ALARM))) {
      await browser.alarms.create(WAKE_ALARM, { periodInMinutes: 0.5 });
    }
    const [stored, session] = await Promise.all([
      browser.storage.local.get([STORAGE_KEYS.port, STORAGE_KEYS.token]),
      browser.storage.session.get([STORAGE_KEYS.railState, STORAGE_KEYS.lastWindowId]),
    ]);
    daemonPort = parsePort(stored[STORAGE_KEYS.port]) ?? DEFAULT_DAEMON_PORT;
    const t = stored[STORAGE_KEYS.token];
    token = typeof t === 'string' && t ? t : null;
    const saved = session[STORAGE_KEYS.railState] as Partial<RailState> | undefined;
    // Restore what the rail last showed, but never a stale connection status.
    state = { ...initialRailState(), ...saved, connection: 'offline', paired: token !== null };
    const win = session[STORAGE_KEYS.lastWindowId];
    if (typeof win === 'number') lastWindowId = win;
  })();

  void ready.then(async () => {
    await refreshOrigin();
    connect();
  });
});
