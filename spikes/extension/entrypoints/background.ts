// Spike 3: service worker owns a WebSocket to a local server.
// - New random bootId every time this script is evaluated (= every SW start).
// - Connects on startup, replies to {"type":"ping"} with {"type":"pong"}.
// - Reconnects with exponential backoff (1 s .. 30 s) on close.
// - Every lifecycle event is queued and flushed to the server so the server log
//   is the single source of truth.
// - A 1-minute browser.alarms tick wakes the SW if it was terminated, so a
//   restart shows up as a new bootId in the server log.

const PORTS: Record<string, number> = {
  production: 8765, // main 30-min run, server sends app-level pings every 20 s
  wsnone: 8766, // negative test: server sends nothing
  wsproto: 8767, // negative test: only WS protocol-level ping frames
  e2e: 8799, // Playwright spike 1/2 runs: nothing listens, keeps them out of the spike-3 logs
};

export default defineBackground(() => {
  const mode = import.meta.env.MODE;
  const port = PORTS[mode] ?? 8765;
  const url = `ws://127.0.0.1:${port}/sw`;
  const bootId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const bootAt = Date.now();
  const uptime = () => Math.round((Date.now() - bootAt) / 1000);

  let ws: WebSocket | null = null;
  let attempt = 0;
  let connSeq = 0;
  const queue: object[] = [];

  function emit(event: string, extra: Record<string, unknown> = {}) {
    const rec = { type: 'event', event, bootId, uptime: uptime(), at: new Date().toISOString(), ...extra };
    console.log('[sidecall-sw]', rec);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(rec));
    else queue.push(rec);
  }

  function connect() {
    connSeq += 1;
    const thisConn = connSeq;
    const sock = new WebSocket(url);
    ws = sock;
    sock.onopen = () => {
      attempt = 0;
      sock.send(JSON.stringify({ type: 'hello', bootId, uptime: uptime(), conn: thisConn, mode }));
      while (queue.length) sock.send(JSON.stringify(queue.shift()));
      emit('ws_open', { conn: thisConn });
    };
    sock.onmessage = (ev) => {
      let msg: { type?: string; seq?: number } = {};
      try {
        msg = JSON.parse(String(ev.data));
      } catch {
        /* ignore */
      }
      if (msg.type === 'ping') {
        sock.send(JSON.stringify({ type: 'pong', seq: msg.seq, bootId, uptime: uptime() }));
      }
    };
    sock.onerror = () => {
      // onclose always follows; nothing to do here except note it.
      queue.push({ type: 'event', event: 'ws_error', bootId, uptime: uptime(), conn: thisConn });
    };
    sock.onclose = (ev) => {
      if (ws === sock) ws = null;
      attempt += 1;
      const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt - 1, 5));
      queue.push({
        type: 'event',
        event: 'ws_close',
        bootId,
        uptime: uptime(),
        conn: thisConn,
        code: ev.code,
        reason: ev.reason,
        wasClean: ev.wasClean,
        retryInMs: delay,
      });
      setTimeout(connect, delay);
    };
  }

  // Panel opens when the toolbar icon is clicked.
  browser.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

  browser.runtime.onInstalled.addListener((d) => emit('onInstalled', { reason: d.reason }));
  browser.runtime.onStartup.addListener(() => emit('onStartup'));
  browser.alarms.create('tick', { periodInMinutes: 1 });
  browser.alarms.onAlarm.addListener((a) => emit('alarm', { name: a.name, wsState: ws?.readyState ?? -1 }));
  browser.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.target === 'background' && msg.type === 'sw-status') {
      sendResponse({ bootId, uptime: uptime(), wsState: ws?.readyState ?? -1, url });
    }
    return false;
  });

  emit('boot', { mode, url, ua: navigator.userAgent });
  connect();
});
