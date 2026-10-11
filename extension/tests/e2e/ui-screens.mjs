// Screenshots of the built extension's UI for design review (not a test).
//
//   pnpm build && node --experimental-transform-types tests/e2e/ui-screens.mjs
//
// Drives the side panel (opened as a tab at 400x800) against the fake daemon, headless, and writes
// light and dark PNGs of each state to extension/.output/screens/.
import { existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright';
import { FakeDaemon, serverMsg } from './fake-daemon.ts';

const EXT_DIR = resolve(import.meta.dirname, '../../.output/chrome-mv3');
const OUT = resolve(import.meta.dirname, '../../.output/screens');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (!existsSync(join(EXT_DIR, 'manifest.json'))) throw new Error('Run `pnpm build` first');
mkdirSync(OUT, { recursive: true });

function task(id, title, patch = {}) {
  return {
    id,
    title,
    brief: { title, goal: title, targets: [], changes: [], acceptanceCriteria: [], outOfScope: [], notes: null },
    status: 'queued',
    createdAt: Date.now(),
    startedAt: null,
    finishedAt: null,
    summary: null,
    files: [],
    error: null,
    lastEvent: null,
    page: null,
    pickIds: [],
    ...patch,
  };
}

const daemon = await FakeDaemon.start();
const app = createServer((_req, res) => {
  res.setHeader('content-type', 'text/html');
  res.end('<!doctype html><title>Fixture</title><h1>Home</h1>');
});
await new Promise((r) => app.listen(0, 'localhost', () => r()));
const appOrigin = `http://localhost:${app.address().port}`;

// Uploads can be held back so a pick stays in its "capturing" state.
let holdUploads = false;
const handleHttp = daemon.handleHttp.bind(daemon);
daemon.handleHttp = (req, res) => (holdUploads ? undefined : handleHttp(req, res));

const profile = mkdtempSync(join(tmpdir(), 'sidecall-screens-'));
const ctx = await chromium.launchPersistentContext(profile, {
  channel: 'chromium',
  headless: true,
  viewport: { width: 400, height: 800 },
  args: ['--headless=new', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', `--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
});

const written = [];
async function shot(page, name) {
  for (const scheme of ['light', 'dark']) {
    await page.emulateMedia({ colorScheme: scheme });
    await sleep(250);
    const path = join(OUT, `${name}-${scheme}.png`);
    await page.screenshot({ path });
    written.push(path);
  }
}

try {
  const sw = ctx.serviceWorkers()[0] ?? (await ctx.waitForEvent('serviceworker'));
  const extId = new URL(sw.url()).host;
  const setPort = (port) => sw.evaluate((p) => chrome.storage.local.set({ daemonPort: p }), port);

  // 1. Offline: point the worker at a port nobody listens on.
  await setPort(1);
  const appPage = await ctx.newPage();
  await appPage.goto(`${appOrigin}/`);
  const panel = await ctx.newPage();
  await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
  await appPage.bringToFront();
  await panel.getByText('Daemon offline').first().waitFor();
  await shot(panel, '01-offline');

  // 2. Pairing.
  await setPort(daemon.port);
  await panel.locator('.rail[data-view="pairing"]').waitFor();
  await shot(panel, '02-pairing');

  // 3. Repo setup, with an inline error.
  await panel.getByLabel('Pairing code').fill('123456');
  await panel.getByRole('button', { name: 'Pair' }).click();
  await panel.getByText('Which repo is this?').waitFor();
  await panel.getByLabel('Repo path').fill('relative/path');
  await panel.getByRole('button', { name: 'Use path' }).click();
  await panel.getByText('Not a directory: relative/path').waitFor();
  await shot(panel, '03-repo-setup');
  await panel.getByLabel('Repo path').fill('/Users/me/code/storefront');
  await panel.getByRole('button', { name: 'Use path' }).click();
  await panel.locator('.rail[data-view="main"]').waitFor();
  await sleep(300);
  await shot(panel, '04-main-empty');

  // 4. Picks, then a conversation that refers to them.
  const anchor = { tag: 'h1', source: null, selector: 'h1', altSelectors: [], textFingerprint: 'home', repeatIndex: null, region: null };
  const fromPage = (msg) => panel.evaluate((m) => chrome.runtime.sendMessage({ kind: 'sidecall-picker-msg', msg: m }), msg);
  async function pickMsg(localId, component, file, line) {
    const viewport = await appPage.evaluate(() => ({ w: innerWidth, h: innerHeight, dpr: devicePixelRatio }));
    const rect = { x: 20, y: 20, w: 200, h: 50 };
    return {
      t: 'pick',
      localId,
      anchor,
      rect,
      viewport,
      draft: {
        kind: 'element',
        page: { url: appPage.url(), route: '/', title: 'Fixture', viewport, scroll: { x: 0, y: 0 }, colorScheme: 'light', consoleErrors: [] },
        element: { selector: 'h1', altSelectors: [], tag: 'h1', attributes: { class: 'title big' }, text: 'Home', outerHTML: '<h1>Home</h1>', rect, computedStyles: {} },
        region: null,
        framework: {
          name: 'svelte',
          version: '5',
          source: { file, line, column: 1, precision: 'element' },
          componentChain: [{ name: component, type: null, file, line: 1, column: 1 }],
          props: {},
        },
        repeat: { count: 4, index: 1 },
      },
    };
  }
  const ready = (id) => panel.locator(`[data-pick-id="${id}"][data-status="ready"]`).waitFor();
  await fromPage(await pickMsg('l1', 'ProductCard', 'src/lib/ProductCard.svelte', 14));
  await ready(1);
  await fromPage(await pickMsg('l2', 'Sidebar', 'src/lib/Sidebar.svelte', 8));
  await ready(2);

  const say = (turnId, role, text, taskId = null) => daemon.broadcast(serverMsg('transcript.final', { turnId, role, text, pickIds: [], taskId }));
  say('u1', 'user', 'make [1] match the spacing of [2]');
  say('a1', 'agent', 'Sure. I will tighten the padding on the product card so it lines up with the sidebar.');
  say('s1', 'system', 'Task queued: Align card spacing', 't1');
  say('u2', 'user', 'and use the same border colour');
  daemon.broadcast(serverMsg('agent.text.delta', { turnId: 'a2', delta: 'Got it, I will reuse the sidebar border token' }));
  await panel.locator('[data-role="agent"][data-streaming="true"]').waitFor();
  await sleep(300);
  await shot(panel, '05-conversation');
  // The chat must scroll once it overflows (it didn't, at first).
  const scroll = await panel.evaluate(() => {
    const log = document.querySelector('[role="log"]');
    const box = log?.firstElementChild;
    if (!(box instanceof HTMLElement)) return 'no conversation';
    // Clone the real entries until they overflow, then try to scroll.
    const originals = [...box.children];
    const clones = [];
    for (let i = 0; i < 12; i++) for (const c of originals) clones.push(box.appendChild(c.cloneNode(true)));
    box.scrollTop = 1000;
    const moved = box.scrollTop;
    for (const c of clones) c.remove();
    return moved > 0 ? `scrolls (scrollTop ${moved})` : `does not scroll (overflow-y ${getComputedStyle(box).overflowY})`;
  });
  console.log('chat:', scroll);
  // 5. Task queue: running, queued, done (and a failed one).
  const running = task('t1', 'Align card spacing', { status: 'running', lastEvent: 'Reading src/lib/Sidebar.svelte' });
  daemon.broadcast(
    serverMsg('queue.state', {
      tasks: [
        task('t0', 'Rename the Buy button', { status: 'completed', summary: 'Renamed to "Add to cart" in 2 files.' }),
        task('t9', 'Install icon pack', { status: 'failed', error: 'pnpm exited with code 1' }),
        running,
        task('t2', 'Use border token on the sidebar'),
        task('t3', 'Add a loading skeleton to the list'),
      ],
    }),
  );
  daemon.broadcast(serverMsg('task.event', { taskId: 't1', kind: 'tool', text: 'Reading src/lib/Sidebar.svelte' }));
  daemon.broadcast(serverMsg('task.event', { taskId: 't1', kind: 'edit', text: 'editing src/lib/ProductCard.svelte' }));
  await panel.locator('[data-task-id="t1"]').waitFor();
  await sleep(300);
  await shot(panel, '06-task-queue');

  // 6. Pick tray with a stale and a capturing chip.
  await fromPage({ t: 'pin-status', id: 2, status: 'stale', rect: null });
  await panel.locator('[data-pick-id="2"][data-stale="true"]').waitFor();
  holdUploads = true;
  await fromPage(await pickMsg('l3', 'Footer', 'src/lib/Footer.svelte', 3));
  await panel.locator('[data-pick-id="3"][data-status="capturing"]').waitFor();
  await shot(panel, '07-pick-tray');
  holdUploads = false;

  // 7. Approval card.
  daemon.broadcast(
    serverMsg('task.blocked', {
      taskId: 't1',
      approval: { id: 'ap1', taskId: 't1', command: 'pnpm add @lucide/svelte', reason: 'Package install needs approval' },
    }),
  );
  await panel.locator('[data-approval-id="ap1"]').waitFor();
  await sleep(300);
  await shot(panel, '08-approval');
  await panel.getByRole('button', { name: 'Deny' }).click();
  await panel.locator('[data-approval-id="ap1"]').waitFor({ state: 'detached' });

  // 8. Options page (while paired), then a call to show the header states.
  const options = await ctx.newPage();
  await options.setViewportSize({ width: 800, height: 800 });
  await options.goto(`chrome-extension://${extId}/options.html`);
  await options.getByText('Connected and paired').waitFor();
  const grant = options.getByRole('button', { name: 'Grant microphone' });
  await options.locator('[data-mic]:not([data-mic="unknown"])').waitFor();
  await shot(options, '12-options');
  if (await grant.isVisible()) await grant.click();
  await options.locator('[data-mic="granted"]').waitFor();
  await options.close();
  await appPage.bringToFront();

  daemon.onOffer = (ws, m) => {
    void (async () => {
      const sdp = await appPage.evaluate(async (offer) => {
        const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
        const pc = new RTCPeerConnection();
        for (const t of mic.getTracks()) pc.addTrack(t, mic);
        await pc.setRemoteDescription({ type: 'offer', sdp: offer });
        await pc.setLocalDescription(await pc.createAnswer());
        await new Promise((r) => {
          if (pc.iceGatheringState === 'complete') return r();
          pc.onicegatheringstatechange = () => pc.iceGatheringState === 'complete' && r();
          setTimeout(r, 2000);
        });
        return pc.localDescription?.sdp ?? '';
      }, m.payload.sdp);
      daemon.send(ws, serverMsg('webrtc.answer', { sdp }));
    })();
  };
  const header = panel.locator('header');
  await header.getByRole('button', { name: 'Start call' }).click();
  await header.getByText('Connecting…').first().waitFor();
  await shot(panel, '09-call-connecting');
  for (const state of ['listening', 'thinking', 'speaking']) {
    daemon.broadcast(serverMsg('call.state', { state }));
    await header.locator(`[data-call-state="${state}"]`).waitFor();
    await shot(panel, `10-call-${state}`);
  }
  daemon.broadcast(serverMsg('call.state', { state: 'listening' }));
  await header.locator('[data-call-state="listening"]').waitFor();
  await header.getByRole('button', { name: 'Mute' }).click();
  await header.getByText('Muted').waitFor();
  await shot(panel, '11-call-muted');

  console.log(written.join('\n'));
} finally {
  await ctx.close();
  await daemon.close();
  await new Promise((r) => app.close(() => r()));
  rmSync(profile, { recursive: true, force: true });
}
