// Service worker: the single owner of the daemon WebSocket (SPEC §5.6, §9.1).
//
// - Connects to ws://127.0.0.1:<port>/ws, answers every JSON ping with a pong
//   (only data messages keep an MV3 worker alive), reconnects with backoff and
//   is woken every 30 s by an alarm when the daemon is down.
// - Pairs (pair.request → pair.result), then authenticates with hello. Sends
//   hello again on the same socket whenever the active tab's origin changes.
// - Keeps RailState via the pure reducer, persists it to storage.session and
//   pushes it to every connected page.
// - Attaches page context (URL, route, title, viewport and a screenshot uploaded
//   to POST /api/upload) to every user.text turn.
// - Runs the pick lifecycle (SPEC §5.2, §5.3, §6.9): ids, clock, screenshots,
//   pick.add / pick.remove / picks.clear, pins on the page, dim and consume.
// - Owns the voice call (SPEC §5.5): creates the offscreen document on call start,
//   relays webrtc.offer / webrtc.answer between it and the daemon, closes it on
//   hang-up, socket loss, a daemon idle or an offscreen error.
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
  type PageDraft,
  type WorkerMessage,
} from '@/lib/messaging';
import {
  PICKER_RUNTIME_KIND,
  type Anchor,
  type ExtToPage,
  type PinSpec,
  type PageToExt,
  type PickerRuntimeDown,
  type PickerRuntimeUp,
} from '@/lib/picker-protocol';
import {
  CALL_RUNTIME_KIND,
  OFFSCREEN_PATH,
  isCallUp,
  type CallDown,
  type CallRuntimeDown,
  type CallUp,
} from '@/lib/call-protocol';
import { AckWaiter } from '@/lib/picks/ack';
import { buildChip, buildPin } from '@/lib/picks/chip';
import { createCaptureGate } from '@/lib/picks/gate';
import { callPageSent, callPageStep, initialCallPageState, type CallPageEvent } from '@/lib/call-page';
import { derivePickImages } from '@/lib/picks/images';
import { daemonTime, nextPickId, picksToDimForTurn, selectPickIds } from '@/lib/picks/turn';
import { allowedOrigin } from '@/lib/origin';
import {
  SCREENSHOT_QUALITY,
  SCREENSHOT_TIMEOUT_MS,
  downscaleSize,
  pageContextFor,
  withTimeout,
} from '@/lib/page-context';
import {
  envelope,
  envelopeFromDraft,
  newMessageId,
  parseServerMessage,
  type ClientMessage,
  type NotifyPayload,
  type PageContext,
  type Pick as PickPayload,
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
/** Give up on one captureVisibleTab round trip after this, so the gate never wedges. */
const PICK_CAPTURE_TIMEOUT_MS = 5_000;
const PAGE_LOG_LIMIT = 200;

/** A live pick's full payload and anchor, kept so it can be re-sent and re-pinned. */
interface StoredPick {
  pick: PickPayload;
  anchor: Anchor;
  /** Screenshots settled (or given up on): pick.add may be sent. */
  ready: boolean;
}

interface TabRef {
  id: number;
  windowId: number;
  url: string;
}

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

  // Picks
  let pickCounter = 0;
  let pickStore: Record<number, StoredPick> = {};
  /** In-flight pick pipelines (capture + upload); never reject. */
  const pendingPicks = new Map<number, Promise<void>>();
  const overlayAcks = new AckWaiter();
  const captureGate = createCaptureGate();
  /**
   * Test seam: every message sent to a page, newest last. Reachable from the
   * worker's DevTools / Playwright as `globalThis.__sidecall.pageLog`.
   */
  const pageLog: { tabId: number; msg: ExtToPage }[] = [];
  /** Test seam: every message from the offscreen call document, newest last. */
  const callLog: CallUp[] = [];
  (globalThis as { __sidecall?: unknown }).__sidecall = { pageLog, callLog };

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
      // The daemon drops its half of the call with the socket.
      if (state.inCall) void endCall({ notifyDaemon: false, error: { code: 'call_lost', message: 'The call ended: lost the connection to the daemon.' } });
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
        if (state.inCall && isCallError(msg.payload.code)) {
          void endCall({ notifyDaemon: false, error: { code: msg.payload.code, message: msg.payload.message } });
          return;
        }
        if (msg.payload.code === 'unauthorized') {
          token = null;
          void browser.storage.local.remove(STORAGE_KEYS.token);
        }
        break;
      case 'notify':
        showNotification(msg.payload);
        break;
      case 'task.created':
        consumePicks(msg.payload.pickIds);
        break;
      case 'transcript.partial':
        callPageTrigger('partial');
        break;
      case 'transcript.final':
        callPageTrigger('final');
        // A spoken turn: the daemon bound it to picks by time; dim them like a typed turn.
        dimPicks(picksToDimForTurn(state.picks, msg.payload));
        break;
      case 'webrtc.answer':
        if (state.inCall) toOffscreen({ op: 'answer', sdp: msg.payload.sdp });
        return;
      case 'call.state':
        // The daemon ended the call (or never had one): tear ours down.
        if (msg.payload.state === 'idle' && state.inCall) {
          void endCall({ notifyDaemon: false });
          return;
        }
        if (msg.payload.state === 'listening' && state.inCall && state.callState !== 'listening') {
          setState(applyServerMessage(state, msg));
          callPageTrigger('listening');
          return;
        }
        break;
    }
    setState(applyServerMessage(state, msg));
    if (msg.type === 'welcome') afterWelcome();
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

  // --- Page context for user turns -------------------------------------------

  async function captureAndUpload(
    windowId: number,
  ): Promise<{ ref: string; bitmapWidth: number }> {
    const dataUrl = await captureGate.run(() =>
      browser.tabs.captureVisibleTab(windowId, { format: 'jpeg', quality: 80 }),
    );
    let blob = await (await fetch(dataUrl)).blob();
    const bitmap = await createImageBitmap(blob);
    const bitmapWidth = bitmap.width;
    try {
      const size = downscaleSize(bitmap.width, bitmap.height);
      if (size.scaled) {
        const canvas = new OffscreenCanvas(size.width, size.height);
        const ctx = canvas.getContext('2d');
        if (!ctx) throw new Error('no 2d context');
        ctx.drawImage(bitmap, 0, 0, size.width, size.height);
        blob = await canvas.convertToBlob({ type: 'image/jpeg', quality: SCREENSHOT_QUALITY });
      }
    } finally {
      bitmap.close();
    }
    return { ref: await uploadBlob(blob), bitmapWidth };
  }

  /** POST /api/upload; resolves to the daemon's image ref. */
  async function uploadBlob(blob: Blob): Promise<string> {
    const res = await fetch(`http://127.0.0.1:${daemonPort}/api/upload`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token ?? ''}`, 'Content-Type': blob.type || 'image/jpeg' },
      body: blob,
    });
    if (!res.ok) throw new Error(`upload failed: HTTP ${res.status}`);
    const body = (await res.json()) as { ref?: unknown };
    if (typeof body.ref !== 'string') throw new Error('upload returned no ref');
    return body.ref;
  }

  /**
   * The page the user is looking at: the active tab of the last focused window,
   * if it's on the bound origin. Never throws; a failed screenshot just leaves
   * `screenshot` null (captureVisibleTab is rate limited to ~2 calls/s).
   */
  async function gatherPage(): Promise<PageContext | null> {
    const bound = state.repo?.origin ?? null;
    let tab: Browser.tabs.Tab | undefined;
    try {
      [tab] = await browser.tabs.query({ active: true, lastFocusedWindow: true });
    } catch {
      return null;
    }
    if (!tab) return null;
    const base = pageContextFor(tab, bound);
    if (!base || tab.windowId === undefined) return base;
    try {
      const capture = await withTimeout(captureAndUpload(tab.windowId), SCREENSHOT_TIMEOUT_MS, 'screenshot');
      return pageContextFor(tab, bound, capture);
    } catch (err) {
      console.warn('[sidecall] screenshot skipped:', err);
      return base;
    }
  }

  // --- Page context for spoken turns (call.page) ------------------------------

  let callPage = initialCallPageState;
  let callPageTimer: ReturnType<typeof setTimeout> | null = null;

  function resetCallPage(): void {
    callPage = initialCallPageState;
    if (callPageTimer !== null) clearTimeout(callPageTimer);
    callPageTimer = null;
  }

  function callPageTrigger(ev: CallPageEvent): void {
    if (!state.inCall) return;
    const step = callPageStep(callPage, ev, Date.now());
    callPage = step.state;
    if (step.action.kind === 'now') sendCallPage();
    else if (step.action.kind === 'after' && callPageTimer === null) {
      callPageTimer = setTimeout(() => {
        callPageTimer = null;
        sendCallPage();
      }, step.action.ms);
    }
  }

  /** Gather the page (screenshot included, through the capture gate) and send it; never blocks. */
  function sendCallPage(): void {
    if (!state.inCall || !token) return;
    callPage = callPageSent(callPage, Date.now());
    void gatherPage().then((page) => {
      // gatherPage is null off the bound origin: skip.
      if (page && state.inCall && token) send(envelope('call.page', { page }));
    });
  }

  // user.text turns go out in order, each after its page context is gathered.
  let turnChain: Promise<void> = Promise.resolve();

  function sendUserText(draft: Extract<PageDraft, { type: 'user.text' }>): void {
    // The worker decides which picks ride along (SPEC §6.9): every live pick not yet
    // used in a turn, plus each [n] the text mentions.
    const wanted = selectPickIds(state.picks, draft.payload.text);
    turnChain = turnChain.then(async () => {
      // A pick still capturing must reach the daemon (pick.add) before the turn that cites it.
      await Promise.all(wanted.map((id) => pendingPicks.get(id)));
      const pickIds = wanted.filter(livePick);
      const page = await gatherPage().catch(() => null);
      if (!token || !send(envelopeFromDraft({ ...draft, payload: { ...draft.payload, pickIds, page } }))) {
        local({ kind: 'error', error: { code: 'not_connected', message: 'Not connected to the daemon.' } });
        return;
      }
      dimPicks(pickIds);
    });
  }

  // --- Picks (SPEC §5.2, §5.3, §6.9) ------------------------------------------

  const livePick = (id: number) => state.picks.some((p) => p.id === id);

  function persistPicks(): void {
    void browser.storage.session.set({
      [STORAGE_KEYS.pickStore]: pickStore,
      [STORAGE_KEYS.pickCounter]: pickCounter,
    });
  }

  /** Tabs currently on the bound origin: the only ones that draw pins. */
  async function originTabs(): Promise<number[]> {
    const bound = state.repo?.origin ?? null;
    if (!bound) return [];
    try {
      const tabs = await browser.tabs.query({});
      return tabs.flatMap((t) => (t.id !== undefined && allowedOrigin(t.url) === bound ? [t.id] : []));
    } catch {
      return [];
    }
  }

  /** Message one tab's picker bridge. False when nothing is listening (no picker in that tab). */
  async function sendToTab(tabId: number, msg: ExtToPage): Promise<boolean> {
    pageLog.push({ tabId, msg });
    if (pageLog.length > PAGE_LOG_LIMIT) pageLog.splice(0, pageLog.length - PAGE_LOG_LIMIT);
    const down: PickerRuntimeDown = { kind: PICKER_RUNTIME_KIND, msg };
    try {
      await browser.tabs.sendMessage(tabId, down);
      return true;
    } catch {
      return false;
    }
  }

  async function sendToOriginTabs(msg: ExtToPage): Promise<void> {
    await Promise.all((await originTabs()).map((id) => sendToTab(id, msg)));
  }

  /** Ask the page to hide/show its overlay and wait (<= 500 ms) for the ack. */
  async function setOverlay(tabId: number, visible: boolean): Promise<boolean> {
    const reqId = newMessageId();
    const acked = overlayAcks.wait(reqId);
    if (!(await sendToTab(tabId, { t: 'overlay-set', reqId, visible }))) {
      overlayAcks.cancel(reqId);
      return false;
    }
    return acked;
  }

  /** ONE captureVisibleTab per pick, with the page's overlay hidden for just that moment. */
  function captureForPick(tab: TabRef): Promise<Blob> {
    return captureGate.run(async () => {
      await setOverlay(tab.id, false);
      try {
        const grab = async () => {
          const dataUrl = await browser.tabs.captureVisibleTab(tab.windowId, { format: 'jpeg', quality: 80 });
          return (await fetch(dataUrl)).blob();
        };
        return await withTimeout(grab(), PICK_CAPTURE_TIMEOUT_MS, 'pick capture');
      } finally {
        await setOverlay(tab.id, true);
      }
    });
  }

  function handlePick(tab: TabRef, msg: Extract<PageToExt, { t: 'pick' }>): void {
    const id = nextPickId(pickCounter, state.picks.map((p) => p.id));
    pickCounter = id;
    const createdAt = daemonTime(Date.now(), state.clockOffset);
    pickStore[id] = { pick: { ...msg.draft, id, createdAt, screenshots: null }, anchor: msg.anchor, ready: false };
    local({ kind: 'pickAdd', chip: buildChip(id, msg.draft), origin: state.repo?.origin ?? null });
    persistPicks();
    const job = runPickPipeline(tab, id, msg)
      .catch((err: unknown) => console.warn('[sidecall] pick pipeline failed:', err))
      .finally(() => pendingPicks.delete(id));
    pendingPicks.set(id, job);
  }

  /**
   * capture → pin on the page → derive crop + viewport → upload → pick.add.
   * A failed capture or upload still ends in pick.add with screenshots: null.
   */
  async function runPickPipeline(tab: TabRef, id: number, msg: Extract<PageToExt, { t: 'pick' }>): Promise<void> {
    let capture: Blob | null = null;
    try {
      capture = await captureForPick(tab);
    } catch (err) {
      console.warn('[sidecall] pick screenshot skipped:', err);
    }
    if (!livePick(id)) return; // removed (Esc / ×) while capturing
    // The overlay is visible again, so the pin can be drawn without photobombing the capture.
    void sendToTab(tab.id, { t: 'pin-add', localId: msg.localId, pin: buildPin(id, msg.draft, msg.anchor, false) });

    let screenshots: PickPayload['screenshots'] = null;
    if (capture) {
      local({ kind: 'pickPatch', id, patch: { status: 'uploading' } });
      try {
        const images = await derivePickImages(capture, msg.rect, { w: msg.viewport.w, h: msg.viewport.h });
        const [crop, viewport] = await Promise.all([uploadBlob(images.crop), uploadBlob(images.viewport)]);
        screenshots = { crop, viewport };
      } catch (err) {
        console.warn('[sidecall] pick screenshot upload failed, sending without images:', err);
      }
    }
    const stored = pickStore[id];
    if (!stored || !livePick(id)) return;
    stored.pick = { ...stored.pick, screenshots };
    stored.ready = true;
    local({ kind: 'pickPatch', id, patch: { status: 'ready' } });
    persistPicks();
    if (token) send(envelope('pick.add', stored.pick));
  }

  /** Remove picks everywhere: daemon, page pins, rail state. */
  function removePicks(ids: number[]): void {
    const live = ids.filter(livePick);
    if (!live.length) return;
    for (const id of live) {
      if (pickStore[id]?.ready && token) send(envelope('pick.remove', { id }));
      delete pickStore[id];
    }
    local({ kind: 'picksRemove', ids: live });
    persistPicks();
    for (const id of live) void sendToOriginTabs({ t: 'pin-remove', id });
  }

  function clearPicks(): void {
    if (token) send(envelope('picks.clear', {}));
    pickStore = {};
    local({ kind: 'picksClear' });
    persistPicks();
    void sendToOriginTabs({ t: 'pins-clear' });
  }

  function dimPicks(ids: number[]): void {
    if (!ids.length) return;
    local({ kind: 'picksDim', ids, dimmed: true });
    void sendToOriginTabs({ t: 'pin-dim', ids, dimmed: true });
  }

  /** task.created consumed these picks: they leave the live set (SPEC §6.9). */
  function consumePicks(ids: number[]): void {
    const used = ids.filter(livePick);
    if (!used.length) return;
    for (const id of used) delete pickStore[id];
    persistPicks();
    for (const id of used) void sendToOriginTabs({ t: 'pin-remove', id });
  }

  function pinsFor(): PinSpec[] {
    return state.picks.flatMap((chip) => {
      const stored = pickStore[chip.id];
      return stored ? [buildPin(chip.id, stored.pick, stored.anchor, chip.dimmed)] : [];
    });
  }

  /** After a (re)bind: the daemon starts empty, so re-send every settled pick (idempotent there). */
  function afterWelcome(): void {
    for (const id of Object.keys(pickStore).map(Number)) if (!livePick(id)) delete pickStore[id];
    for (const stored of Object.values(pickStore)) {
      if (stored.ready && livePick(stored.pick.id)) send(envelope('pick.add', stored.pick));
    }
    persistPicks();
    if (state.picks.length) void sendToOriginTabs({ t: 'pins-restore', pins: pinsFor() });
  }

  /**
   * The tab a picker message is about: the sender's tab, or (test seam, and for
   * messages from our own extension pages) the active tab of the last focused window.
   */
  async function resolveTab(sender: Browser.runtime.MessageSender): Promise<TabRef | null> {
    const t = sender.tab;
    if (t?.id !== undefined && t.windowId !== undefined && t.url && !t.url.startsWith(ownPagePrefix)) {
      return { id: t.id, windowId: t.windowId, url: t.url };
    }
    try {
      const [active] = await browser.tabs.query({ active: true, lastFocusedWindow: true });
      if (active?.id !== undefined && active.windowId !== undefined && active.url) {
        return { id: active.id, windowId: active.windowId, url: active.url };
      }
    } catch {
      // fall through
    }
    return null;
  }

  async function handlePageMessage(msg: PageToExt, sender: Browser.runtime.MessageSender): Promise<void> {
    if (msg.t === 'overlay-ack') {
      overlayAcks.ack(msg.reqId);
      return;
    }
    const tab = await resolveTab(sender);
    const bound = state.repo?.origin ?? null;
    // Picks belong to the bound repo/origin; other tabs get no pins.
    if (!tab || !bound || allowedOrigin(tab.url) !== bound) return;
    switch (msg.t) {
      case 'ready':
        if (state.picks.length) void sendToTab(tab.id, { t: 'pins-restore', pins: pinsFor() });
        return;
      case 'pick':
        handlePick(tab, msg);
        return;
      case 'escape':
        removePicks(state.picks.filter((p) => !p.dimmed).map((p) => p.id));
        return;
      case 'pin-status':
        local({ kind: 'pickPatch', id: msg.id, patch: { stale: msg.status === 'stale' } });
        return;
    }
  }

  function isPickerUp(m: unknown): m is PickerRuntimeUp {
    return !!m && typeof m === 'object' && (m as { kind?: unknown }).kind === PICKER_RUNTIME_KIND && typeof (m as { msg?: { t?: unknown } }).msg?.t === 'string';
  }

  browser.runtime.onMessage.addListener((raw: unknown, sender) => {
    if (!isPickerUp(raw)) return;
    void ready.then(() => handlePageMessage(raw.msg, sender));
  });

  // --- Voice call (SPEC §5.5) --------------------------------------------------

  let creatingOffscreen: Promise<void> | null = null;
  const offscreenUrl = browser.runtime.getURL(OFFSCREEN_PATH);

  /** Daemon error codes that mean the call can't go on. */
  const isCallError = (code: string) => code === 'not_implemented' || code.startsWith('call') || code.startsWith('webrtc') || code === 'no_repo';

  async function hasOffscreen(): Promise<boolean> {
    const api = browser.offscreen as typeof browser.offscreen & { hasDocument?: () => Promise<boolean> };
    if (typeof api.hasDocument === 'function') return api.hasDocument();
    const contexts = await browser.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT' as Browser.runtime.ContextType],
      documentUrls: [offscreenUrl],
    });
    return contexts.length > 0;
  }

  /** Only one offscreen document may exist per extension: guard with hasDocument plus an in-flight promise. */
  async function ensureOffscreen(): Promise<void> {
    if (creatingOffscreen) return creatingOffscreen;
    creatingOffscreen = (async () => {
      if (await hasOffscreen()) return;
      await browser.offscreen.createDocument({
        url: OFFSCREEN_PATH,
        // Never AUDIO_PLAYBACK: it closes the document after 30 s of silence.
        reasons: ['USER_MEDIA' as Browser.offscreen.Reason, 'WEB_RTC' as Browser.offscreen.Reason],
        justification: 'Holds the microphone and the WebRTC audio connection for a Sidecall voice call.',
      });
    })();
    try {
      await creatingOffscreen;
    } finally {
      creatingOffscreen = null;
    }
  }

  async function closeOffscreen(): Promise<void> {
    try {
      if (creatingOffscreen) await creatingOffscreen.catch(() => {});
      if (await hasOffscreen()) await browser.offscreen.closeDocument();
    } catch (err) {
      console.warn('[sidecall] closing the offscreen document failed', err);
    }
  }

  function toOffscreen(msg: CallDown): void {
    const wrapped: CallRuntimeDown = { kind: CALL_RUNTIME_KIND, to: 'offscreen', msg };
    void browser.runtime.sendMessage(wrapped).catch(() => {});
  }

  async function startCall(): Promise<void> {
    if (state.inCall) return;
    if (!token || !isOpen() || !state.repo) {
      local({ kind: 'error', error: { code: 'not_connected', message: 'Connect to a repo to start a call.' } });
      return;
    }
    resetCallPage();
    local({ kind: 'callStarting' });
    send(envelope('call.start', {}));
    try {
      // The document starts the mic and sends its offer as soon as it loads.
      await ensureOffscreen();
    } catch (err) {
      await endCall({ error: { code: 'offscreen_failed', message: `Could not start the call: ${err instanceof Error ? err.message : String(err)}` } });
    }
  }

  async function endCall(opts: { notifyDaemon?: boolean; error?: { code: string; message: string } } = {}): Promise<void> {
    const wasInCall = state.inCall;
    resetCallPage();
    local({ kind: 'callEnded', ...(opts.error ? { error: opts.error } : {}) });
    if (wasInCall && opts.notifyDaemon !== false && token) send(envelope('call.end', {}));
    await closeOffscreen();
  }

  function setMuted(muted: boolean): void {
    if (!state.inCall) return;
    local({ kind: 'callMuted', muted });
    toOffscreen({ op: 'mute', muted });
    if (token) send(envelope('call.mute', { muted }));
  }

  function handleCallUp(msg: CallUp): void {
    callLog.push(msg);
    if (callLog.length > PAGE_LOG_LIMIT) callLog.splice(0, callLog.length - PAGE_LOG_LIMIT);
    if (!state.inCall) {
      // A document left over from an earlier call (e.g. after a worker restart).
      void closeOffscreen();
      return;
    }
    switch (msg.ev) {
      case 'offer':
        if (!send(envelope('webrtc.offer', { sdp: msg.sdp }))) {
          void endCall({ notifyDaemon: false, error: { code: 'call_lost', message: 'The call ended: lost the connection to the daemon.' } });
        }
        return;
      case 'answered':
      case 'connection':
        return;
      case 'error':
        void endCall({ error: { code: msg.code, message: msg.message } });
        // Only the options page can grant the mic (SPEC §5.5).
        if (msg.code === 'mic_permission') void browser.runtime.openOptionsPage().catch(() => {});
        return;
    }
  }

  browser.runtime.onMessage.addListener((raw: unknown) => {
    if (!isCallUp(raw)) return;
    void ready.then(() => handleCallUp(raw.msg));
  });

  // --- Notifications ---------------------------------------------------------

  function showNotification(p: NotifyPayload): void {
    if (railOpen()) return;
    // The window id rides in the notification id, so the click handler can call
    // sidePanel.open synchronously, inside the user gesture, even after a restart.
    const id = `${NOTIFICATION_PREFIX}:${lastWindowId ?? -1}:${Date.now()}:${p.action === 'call' ? 'call' : 'open'}`;
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

  function onNotificationClicked(notificationId: string): void {
    const [prefix, win, , action] = notificationId.split(':');
    if (prefix !== NOTIFICATION_PREFIX) return;
    // action "call": also start a call (mic check included); the daemon opens it with a summary.
    if (action === 'call') void ready.then(() => (state.inCall ? undefined : startCall()));
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
  }

  browser.notifications.onClicked.addListener(onNotificationClicked);
  (globalThis as { __sidecall?: Record<string, unknown> }).__sidecall!.onNotificationClicked = onNotificationClicked;

  // --- Pages (rail, options) -------------------------------------------------

  async function handleCommand(cmd: PageCommand): Promise<void> {
    switch (cmd.cmd) {
      case 'send':
        if (!isPageDraft(cmd.draft)) return;
        if (cmd.draft.type === 'user.text') {
          if (!token || !isOpen()) {
            local({ kind: 'error', error: { code: 'not_connected', message: 'Not connected to the daemon.' } });
            return;
          }
          sendUserText(cmd.draft);
          return;
        }
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
        local({ kind: 'clearCallError' });
        return;
      case 'openOptions':
        await browser.runtime.openOptionsPage();
        return;
      case 'callStart':
        await startCall();
        return;
      case 'callEnd':
        await endCall();
        return;
      case 'callMute':
        setMuted(cmd.muted === true);
        return;
      case 'pickRemove':
        if (typeof cmd.id === 'number') removePicks([cmd.id]);
        return;
      case 'picksClear':
        clearPicks();
        return;
      case 'pickHighlight':
        void sendToOriginTabs({ t: 'pin-highlight', id: typeof cmd.id === 'number' ? cmd.id : null });
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
    void ready.then(refreshOrigin).then(() => callPageTrigger('tab'));
  });
  browser.tabs.onUpdated.addListener((_tabId, change, tab) => {
    if (!tab.active || (change.url === undefined && change.status === undefined)) return;
    void ready.then(refreshOrigin).then(() => {
      if (change.status === 'complete') callPageTrigger('tab');
    });
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
    void reinjectPicker();
  });

  /**
   * Chrome doesn't inject content scripts into tabs that were already open when the
   * extension was installed or reloaded, and an old MAIN-world picker keeps running there
   * with a dead bridge (hover works, picks vanish). Inject a fresh bridge and picker; the
   * new picker takes over from any stale one (lib/picker/boot.ts).
   */
  async function reinjectPicker(): Promise<void> {
    let tabs: Browser.tabs.Tab[] = [];
    try {
      tabs = await browser.tabs.query({ url: ['http://localhost/*', 'http://127.0.0.1/*'] });
    } catch {
      return;
    }
    for (const tab of tabs) {
      if (tab.id === undefined || tab.discarded) continue;
      try {
        await browser.scripting.executeScript({ target: { tabId: tab.id }, files: ['/content-scripts/picker-bridge.js'] });
        await browser.scripting.executeScript({ target: { tabId: tab.id }, files: ['/content-scripts/picker.js'], world: 'MAIN' });
      } catch (err) {
        console.warn('[sidecall] re-inject picker failed', tab.url, err);
      }
    }
  }

  const ready = (async () => {
    void browser.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
    if (!(await browser.alarms.get(WAKE_ALARM))) {
      await browser.alarms.create(WAKE_ALARM, { periodInMinutes: 0.5 });
    }
    const [stored, session] = await Promise.all([
      browser.storage.local.get([STORAGE_KEYS.port, STORAGE_KEYS.token]),
      browser.storage.session.get([
        STORAGE_KEYS.railState,
        STORAGE_KEYS.lastWindowId,
        STORAGE_KEYS.pickStore,
        STORAGE_KEYS.pickCounter,
      ]),
    ]);
    daemonPort = parsePort(stored[STORAGE_KEYS.port]) ?? DEFAULT_DAEMON_PORT;
    const t = stored[STORAGE_KEYS.token];
    token = typeof t === 'string' && t ? t : null;
    const saved = session[STORAGE_KEYS.railState] as Partial<RailState> | undefined;
    // Restore what the rail last showed, but never a stale connection status.
    state = { ...initialRailState(), ...saved, connection: 'offline', paired: token !== null };
    // A restart mid-pipeline can't finish the uploads: settle those picks without images.
    const savedStore = session[STORAGE_KEYS.pickStore] as Record<number, StoredPick> | undefined;
    pickStore = {};
    for (const chip of state.picks) {
      const stored = savedStore?.[chip.id];
      if (stored) pickStore[chip.id] = { ...stored, ready: true };
    }
    state = {
      ...state,
      picks: state.picks.filter((c) => pickStore[c.id]).map((c) => (c.status === 'ready' ? c : { ...c, status: 'ready' as const })),
    };
    const counter = session[STORAGE_KEYS.pickCounter];
    pickCounter = Math.max(typeof counter === 'number' ? counter : 0, ...state.picks.map((p) => p.id));
    const win = session[STORAGE_KEYS.lastWindowId];
    if (typeof win === 'number') lastWindowId = win;
    // A restarted worker has lost its socket, and the daemon ended the call with it.
    if (state.inCall) {
      state = applyLocalEvent(state, { kind: 'callEnded', error: { code: 'call_lost', message: 'The call ended when Sidecall restarted.' } });
    }
    state = { ...state, callState: 'idle' };
    void closeOffscreen();
  })();

  void ready.then(async () => {
    await refreshOrigin();
    connect();
  });
});
