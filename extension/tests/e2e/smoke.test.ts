// Playwright smoke test: the built extension in headless Chromium against a fake
// daemon. Run with `pnpm test:e2e` (builds first).
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium, type BrowserContext, type Page, type Worker } from 'playwright';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { Task } from '../../src/lib/protocol';
import { FakeDaemon, serverMsg } from './fake-daemon';

// `sw.evaluate` callbacks run inside the extension's service worker, where `chrome` exists.
declare const chrome: typeof import('wxt/browser').browser;

const EXPECTED_ID = 'kcjkimilmanlonlplfancfpoekkphjhb';
const EXT_DIR = resolve(import.meta.dirname, '../../.output/chrome-mv3');
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function task(id: string, title: string, patch: Partial<Task> = {}): Task {
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

/** Width and height from a JPEG's SOF marker. */
function jpegSize(buf: Buffer): { width: number; height: number } | null {
  let i = 2;
  while (i + 9 < buf.length) {
    if (buf[i] !== 0xff) return null;
    const marker = buf[i + 1] ?? 0;
    const len = buf.readUInt16BE(i + 2);
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      return { height: buf.readUInt16BE(i + 5), width: buf.readUInt16BE(i + 7) };
    }
    i += 2 + len;
  }
  return null;
}

describe('extension smoke', () => {
  let daemon: FakeDaemon;
  let app: Server;
  let appOrigin: string;
  let profile: string;
  let ctx: BrowserContext;
  let sw: Worker;
  let extId: string;
  let appPage: Page;
  let panel: Page;

  beforeAll(async () => {
    if (!existsSync(join(EXT_DIR, 'manifest.json'))) throw new Error('Run `pnpm build` first');
    daemon = await FakeDaemon.start();
    app = createServer((_req, res) => {
      res.setHeader('content-type', 'text/html');
      res.end('<!doctype html><title>Fixture</title><h1>Home</h1>');
    });
    await new Promise<void>((r) => app.listen(0, 'localhost', () => r()));
    appOrigin = `http://localhost:${(app.address() as AddressInfo).port}`;

    profile = mkdtempSync(join(tmpdir(), 'sidecall-e2e-'));
    ctx = await chromium.launchPersistentContext(profile, {
      channel: 'chromium', // full Chromium; its new headless mode supports extensions
      headless: true,
      // A real 2x window (Playwright's deviceScaleFactor doesn't reach
      // captureVisibleTab), so captures exceed the 1568 px cap and get downscaled.
      viewport: null,
      args: ['--headless=new', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--force-device-scale-factor=2', '--window-size=1280,800', `--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
    });
    sw = ctx.serviceWorkers()[0] ?? (await ctx.waitForEvent('serviceworker'));
    extId = new URL(sw.url()).host;
    // Point the worker at the fake daemon; storage.onChanged makes it reconnect.
    await sw.evaluate((port) => chrome.storage.local.set({ daemonPort: port }), daemon.port);
  });

  afterAll(async () => {
    await ctx?.close();
    await daemon?.close();
    await new Promise<void>((r) => (app ? app.close(() => r()) : r()));
    if (profile) rmSync(profile, { recursive: true, force: true });
  });

  it('has the pinned extension ID', () => {
    expect(extId).toBe(EXPECTED_ID);
  });

  it('connects with the extension Origin and answers pings', async () => {
    const pong = await daemon.waitFor('pong');
    expect(pong.payload.seq).toBe(1);
    expect(daemon.origins[0]).toBe(`chrome-extension://${EXPECTED_ID}`);
    // No token yet, so nothing but the pong.
    expect(daemon.of('hello')).toHaveLength(0);
  });

  it('pairs, then asks for the repo of the active localhost tab', async () => {
    appPage = await ctx.newPage();
    await appPage.goto(`${appOrigin}/some/route?tab=a#top`);
    panel = await ctx.newPage();
    await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
    // A new tab starts as about:blank, which (correctly) unbinds; make the app
    // the active tab again. The panel tab keeps rendering in the background.
    await appPage.bringToFront();

    await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('pairing');
    await panel.getByLabel('Pairing code').fill('000000');
    await panel.getByRole('button', { name: 'Pair' }).click();
    await panel.getByText('Wrong or expired code.').waitFor();

    await panel.getByLabel('Pairing code').fill('123 456');
    await panel.getByRole('button', { name: 'Pair' }).click();
    const hello = await daemon.waitFor('hello');
    expect(hello.payload).toMatchObject({ token: 'tok-test', origin: appOrigin, extVersion: '0.1.0' });
    expect(typeof hello.payload.clockSample).toBe('number');
    expect(await sw.evaluate(() => chrome.storage.local.get('token'))).toEqual({ token: 'tok-test' });

    // Unmapped origin → repo setup.
    await panel.getByText('Which repo is this?').waitFor();
    await panel.locator('.blocking code').getByText(appOrigin).waitFor();
    await panel.getByRole('button', { name: 'Choose folder…' }).click();
    await panel.getByText('Folder selection cancelled.').waitFor();
    await panel.getByLabel('Repo path').fill('relative/path');
    await panel.getByRole('button', { name: 'Use path' }).click();
    await panel.getByText('Not a directory: relative/path').waitFor();
    await panel.getByLabel('Repo path').fill('/Users/me/fixtures/svelte5-app');
    await panel.getByRole('button', { name: 'Use path' }).click();
    expect((await daemon.waitFor('repo.map', (m) => m.payload.path.startsWith('/'))).payload.origin).toBe(appOrigin);

    await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('main');
    await panel.getByRole('button', { name: 'Repo menu' }).click();
    await panel.locator('.sc-repo-menu .name').getByText('svelte5-app').waitFor();
    await panel.keyboard.press('Escape');
    expect(await panel.locator('.composer [data-status]').getAttribute('data-status')).toBe('connected');
  });

  it('streams the agent reply, queues a task and approves a blocked step', async () => {
    daemon.onUserText = (ws, m) => {
      void (async () => {
        const send = (msg: Parameters<typeof daemon.send>[1]) => daemon.send(ws, msg);
        send(serverMsg('transcript.final', { turnId: 'u1', role: 'user', text: m.payload.text, pickIds: [], taskId: null }));
        for (const delta of ['Sure — ', 'I’ll make the ', 'heading red.']) {
          send(serverMsg('agent.text.delta', { turnId: 'a1', delta }));
          await sleep(250);
        }
        send(serverMsg('agent.text.done', { turnId: 'a1' }));
        send(serverMsg('transcript.final', { turnId: 'a1', role: 'agent', text: 'Sure — I’ll make the heading red.', pickIds: [], taskId: null }));
        const t1 = task('t1', 'Red heading');
        send(serverMsg('task.created', t1));
        send(serverMsg('queue.state', { tasks: [t1, task('t2', 'Bigger title'), task('t3', 'Footer link')] }));
        send(serverMsg('transcript.final', { turnId: 's1', role: 'system', text: 'Task queued: Red heading', pickIds: [], taskId: 't1' }));
        send(serverMsg('queue.state', { tasks: [{ ...t1, status: 'running' }, task('t2', 'Bigger title'), task('t3', 'Footer link')] }));
        send(serverMsg('task.event', { taskId: 't1', kind: 'edit', text: 'editing src/routes/+page.svelte' }));
        send(
          serverMsg('task.blocked', {
            taskId: 't1',
            approval: { id: 'ap1', taskId: 't1', command: 'pnpm add left-pad', reason: 'Package install needs approval' },
          }),
        );
      })();
    };

    const composer = panel.getByLabel('Message');
    await composer.fill('make the heading on the home page red');
    await composer.press('Enter');
    const userText = await daemon.waitFor('user.text');
    const { page, ...rest } = userText.payload;
    expect(rest).toEqual({ text: 'make the heading on the home page red', pickIds: [] });
    // Page context: the active app tab, with a screenshot uploaded first.
    expect(page).toMatchObject({
      url: `${appOrigin}/some/route?tab=a#top`,
      route: '/some/route?tab=a#top',
      title: 'Fixture',
    });
    expect(page?.viewport.w).toBeGreaterThan(0);
    expect(page?.viewport.h).toBeGreaterThan(0);
    expect(page?.screenshot).toMatch(/^[0-9a-f]{32}$/);
    const shot = daemon.uploads.get(page?.screenshot ?? '');
    expect(shot).toBeDefined();
    expect(shot?.contentType).toBe('image/jpeg');
    expect(shot?.origin).toBe(`chrome-extension://${EXPECTED_ID}`);
    expect(shot?.authorization).toBe('Bearer tok-test');
    expect(shot?.bytes.length).toBeGreaterThan(100);
    expect([...(shot?.bytes.subarray(0, 3) ?? [])]).toEqual([0xff, 0xd8, 0xff]); // JPEG magic
    const dims = jpegSize(shot?.bytes ?? Buffer.alloc(0));
    // 1280 CSS px at 2x captures 2560 px wide; the worker scales it to the 1568 px cap.
    expect(page?.viewport.dpr).toBe(2);
    expect(dims?.width).toBe(1568);
    await expect.poll(() => composer.inputValue()).toBe('');

    await panel.locator('[data-role="user"]').getByText('make the heading on the home page red').waitFor();
    // Mid-stream: one agent bubble, still streaming.
    await panel.locator('[data-role="agent"][data-streaming="true"]').getByText('Sure — I’ll make the').waitFor();
    await panel.locator('[data-role="agent"][data-streaming="false"]').getByText('Sure — I’ll make the heading red.').waitFor();
    expect(await panel.locator('[data-role="agent"]').count()).toBe(1);
    await panel.locator('[data-role="system"]').getByText('Task queued: Red heading').waitFor();

    // Task cards: the running one with its live status line, two queued.
    await panel.locator('[data-task-id="t1"]').getByText('Waiting for your approval').waitFor();
    expect(await panel.locator('.task-stack [data-status="queued"]').count()).toBe(2);

    // Approve the blocked step.
    const card = panel.locator('[data-approval-id="ap1"]');
    await card.getByText('pnpm add left-pad').waitFor();
    await card.getByRole('button', { name: 'Approve' }).click();
    const respond = await daemon.waitFor('approval.respond');
    expect(respond.payload).toEqual({ id: 'ap1', allow: true });
    await card.waitFor({ state: 'detached' });
    await panel.locator('[data-task-id="t1"]').getByText('editing src/routes/+page.svelte').waitFor();
  });

  it('cancels a queued task from its card', async () => {
    await panel.locator('[data-task-id="t3"]').getByRole('button', { name: 'Cancel' }).click();
    expect((await daemon.waitFor('task.cancel')).payload).toEqual({ id: 't3' });
  });

  describe('picks (service worker + rail half)', () => {
    type PageLogEntry = { tabId: number; msg: { t: string; [k: string]: unknown } };
    const pageLog = () => sw.evaluate(() => (globalThis as unknown as { __sidecall: { pageLog: unknown[] } }).__sidecall.pageLog) as Promise<PageLogEntry[]>;
    const logOf = async (t: string) => (await pageLog()).filter((e) => e.msg.t === t);
    /** Stand-in for the page-side bridge: a runtime message from an extension page (tab falls back to the active tab). */
    const fromPage = (msg: unknown) =>
      panel.evaluate((m) => chrome.runtime.sendMessage({ kind: 'sidecall-picker-msg', msg: m }), msg);
    const railCommand = (cmd: unknown) =>
      panel.evaluate(
        (c) =>
          new Promise<void>((r) => {
            const port = chrome.runtime.connect({ name: 'options' });
            port.postMessage(c);
            setTimeout(r, 50);
          }),
        cmd,
      );
    const anchor = { tag: 'h1', source: null, selector: 'h1', altSelectors: [], textFingerprint: 'home', repeatIndex: null, region: null };
    async function pickMsg(localId: string) {
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
            source: { file: 'src/routes/+page.svelte', line: 3, column: 1, precision: 'element' },
            componentChain: [{ name: 'Page', type: null, file: 'src/routes/+page.svelte', line: 1, column: 1 }],
            props: {},
          },
          repeat: { count: 4, index: 1 },
        },
      };
    }
    const pickAdds = () => daemon.of('pick.add');
    /** The next pick.add / pick.remove after `before` messages (ids restart at 1 once the tray empties). */
    const nextOf = async <T extends 'pick.add' | 'pick.remove'>(type: T, before: number) => {
      await expect.poll(() => daemon.of(type).length).toBeGreaterThan(before);
      return daemon.of(type).at(-1)!;
    };

    it('stores the daemon clock offset from welcome', async () => {
      daemon.clockOffset = 60_000;
      await railCommand({ cmd: 'reconnect' });
      await expect.poll(async () => ((await sw.evaluate(() => chrome.storage.session.get('railState'))).railState as { clockOffset?: number } | undefined)?.clockOffset).toBe(60_000);
      await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('main');
    });

    it('a pick shows a chip, uploads crop + viewport and sends pick.add', async () => {
      await panel.getByLabel('Message').waitFor();
      const before = Date.now();
      await fromPage(await pickMsg('l1'));
      const chip = panel.locator('.tray [data-pick-id="1"]');
      await chip.waitFor();
      await expect.poll(() => chip.getAttribute('data-status')).toBe('ready');
      expect(await chip.locator('.num').innerText()).toBe('1');
      expect(await chip.locator('.label').innerText()).toContain('Page');
      expect(await chip.getAttribute('data-tip')).toContain('src/routes/+page.svelte:3:1 (element) · repeated 4×, instance 2');

      const add = await daemon.waitFor('pick.add', (m) => m.payload.id === 1);
      expect(add.payload.createdAt).toBeGreaterThanOrEqual(before + 60_000);
      expect(add.payload.createdAt).toBeLessThan(Date.now() + 60_000 + 1);
      const shots = add.payload.screenshots;
      expect(shots).not.toBeNull();
      const crop = daemon.uploads.get(shots?.crop ?? '');
      const view = daemon.uploads.get(shots?.viewport ?? '');
      expect([...(crop?.bytes.subarray(0, 3) ?? [])]).toEqual([0xff, 0xd8, 0xff]);
      expect([...(view?.bytes.subarray(0, 3) ?? [])]).toEqual([0xff, 0xd8, 0xff]);
      const c = jpegSize(crop?.bytes ?? Buffer.alloc(0));
      const v = jpegSize(view?.bytes ?? Buffer.alloc(0));
      // 232x82 CSS px (rect + 16 px margin) at 2x, native resolution.
      expect(c).toEqual({ width: 464, height: 164 });
      expect(v?.width).toBe(1568);
      expect((c?.width ?? 0) * (c?.height ?? 0)).toBeLessThan((v?.width ?? 1) * (v?.height ?? 1));

      // The page got the overlay hide/show around one capture, then the pin.
      const sent = (await pageLog()).map((e) => e.msg.t + (e.msg.t === 'overlay-set' ? `:${String(e.msg.visible)}` : ''));
      expect(sent.slice(-2)).toEqual(['overlay-set:true', 'pin-add']);
      expect(sent.filter((t) => t === 'overlay-set:false')).toHaveLength(1);
      const pin = (await logOf('pin-add')).at(-1)?.msg.pin as { id: number; label: string; dimmed: boolean };
      expect(pin).toMatchObject({ id: 1, label: 'Page  src/routes/+page.svelte:3', dimmed: false });
      // Without a picker in the tab nothing acks, so the hide was bounded, not hung.
      await panel.getByText('⌥-click elements on the page').waitFor({ state: 'detached' });
    });

    it('tray hover highlights the pin; clicking a chip does not type into the message', async () => {
      await fromPage(await pickMsg('l2'));
      await expect.poll(() => panel.locator('.tray [data-pick-id="2"]').getAttribute('data-status')).toBe('ready');
      await panel.locator('.tray [data-pick-id="1"]').hover();
      await expect.poll(async () => (await logOf('pin-highlight')).at(-1)?.msg.id).toBe(1);
      await panel.mouse.move(5, 5);
      await expect.poll(async () => (await logOf('pin-highlight')).at(-1)?.msg.id).toBeNull();

      const composer = panel.getByLabel('Message');
      await composer.fill('make it match');
      await panel.locator('.tray [data-pick-id="2"] .label').click();
      // Picks ride along with the message; they're never typed into it.
      expect(await composer.inputValue()).toBe('make it match');
      await composer.fill('');
    });

    it('typing [1] and [2] sends pickIds, dims both, and task.created consumes them', async () => {
      daemon.onUserText = (ws, m) => {
        daemon.send(ws, serverMsg('transcript.final', { turnId: 'u9', role: 'user', text: m.payload.text, pickIds: m.payload.pickIds, taskId: null }));
        daemon.send(ws, serverMsg('task.created', task('t9', 'Match', { pickIds: m.payload.pickIds })));
      };
      const composer = panel.getByLabel('Message');
      await composer.fill('make [1] match [2]');
      // Hold the daemon reply back so the dimmed state is observable.
      await composer.press('Enter');
      const ut = await daemon.waitFor('user.text', (m) => m.payload.text === 'make [1] match [2]');
      expect(ut.payload.pickIds).toEqual([1, 2]);
      await expect.poll(async () => (await logOf('pin-dim')).at(-1)?.msg.ids).toEqual([1, 2]);
      // The transcript renders typed [n] as numbered pills with the stored tooltip, and lists
      // the picks the message carried as chips under it.
      const bubble = panel.locator('.entry.user[data-turn-id="u9"]');
      await bubble.waitFor();
      expect(await bubble.locator('.marker').count()).toBe(2);
      expect(await bubble.locator('.marker').first().getAttribute('data-tip')).toContain('src/routes/+page.svelte:3:1');
      expect(await bubble.locator('.marker').first().innerText()).toBe('1');
      expect(await bubble.locator('.pick').count()).toBe(2);
      // task.created with pickIds consumed them.
      await panel.locator('.tray [data-pick-id]').first().waitFor({ state: 'detached' });
      const removed = (await logOf('pin-remove')).map((e) => e.msg.id);
      expect(removed).toEqual(expect.arrayContaining([1, 2]));
      await expect.poll(async () => (await sw.evaluate(() => chrome.storage.session.get('pickStore'))).pickStore).toEqual({});
      daemon.onUserText = null;
    });

    it('re-sends live picks after a reconnect; Esc removes the current turn’s picks', async () => {
      const addsBefore = pickAdds().length;
      await fromPage(await pickMsg('l3'));
      // The previous test's picks were consumed, so numbering is back at [1].
      expect((await nextOf('pick.add', addsBefore)).payload.id).toBe(1);
      await expect.poll(() => panel.locator('.tray [data-pick-id="1"]').getAttribute('data-status')).toBe('ready');
      const n = pickAdds().filter((m) => m.payload.id === 1).length;
      await railCommand({ cmd: 'reconnect' });
      await expect.poll(() => pickAdds().filter((m) => m.payload.id === 1).length).toBe(n + 1);

      const removesBefore = daemon.of('pick.remove').length;
      await fromPage({ t: 'escape' });
      expect((await nextOf('pick.remove', removesBefore)).payload).toEqual({ id: 1 });
      await panel.locator('.tray [data-pick-id="1"]').waitFor({ state: 'detached' });
      expect((await logOf('pin-remove')).map((e) => e.msg.id)).toContain(1);
    });

    it('a failed upload still sends pick.add (screenshots null); restore, stale and Clear all work', async () => {
      daemon.failUploads = true;
      const addsBefore = pickAdds().length;
      await fromPage(await pickMsg('l4'));
      const add = await nextOf('pick.add', addsBefore);
      expect(add.payload.id).toBe(1);
      expect(add.payload.screenshots).toBeNull();
      await expect.poll(() => panel.locator('.tray [data-pick-id="1"]').getAttribute('data-status')).toBe('ready');
      daemon.failUploads = false;

      await fromPage({ t: 'ready', url: appPage.url() });
      await expect.poll(async () => (await logOf('pins-restore')).length).toBeGreaterThan(0);
      const restored = (await logOf('pins-restore')).at(-1)?.msg.pins as { id: number }[];
      expect(restored.map((p) => p.id)).toEqual([1]);

      await fromPage({ t: 'pin-status', id: 1, status: 'stale', rect: null });
      await panel.locator('.tray [data-pick-id="1"][data-stale="true"]').waitFor();

      await panel.getByRole('button', { name: 'Clear all' }).click();
      await daemon.waitFor('picks.clear');
      await panel.locator('.tray [data-pick-id]').first().waitFor({ state: 'detached' });
      expect((await logOf('pins-clear')).length).toBeGreaterThan(0);
    });

    it('a voice transcript.final with pickIds dims those picks', async () => {
      const addsBefore = pickAdds().length;
      await fromPage(await pickMsg('l3'));
      const add = await nextOf('pick.add', addsBefore);
      expect(add.payload.id).toBe(1);
      expect(add.payload.createdAt).toBeGreaterThan(Date.now() + 30_000); // daemon clock
      await expect.poll(() => panel.locator('.tray [data-pick-id="1"]').getAttribute('data-status')).toBe('ready');
      daemon.broadcast(serverMsg('transcript.final', { turnId: 'v1', role: 'user', text: 'make this blue', pickIds: [1], taskId: null }));
      await expect.poll(async () => (await logOf('pin-dim')).at(-1)?.msg.ids).toEqual([1]);
      await panel.locator('.tray [data-pick-id="1"][data-dimmed="true"]').waitFor();
      // The sent message shows the pick it carried.
      await panel.locator('.entry.user[data-turn-id="v1"] .pick[data-pick-id="1"]').waitFor();
      await panel.getByRole('button', { name: 'Clear all' }).click();
      await panel.locator('.tray [data-pick-id]').first().waitFor({ state: 'detached' });
    });

    it('ignores picks from a tab that is not on the bound origin', async () => {
      const other = await ctx.newPage();
      await other.goto('data:text/html,<title>x</title>');
      await other.bringToFront();
      const adds = pickAdds().length;
      await fromPage(await pickMsg('l5'));
      await sleep(400);
      expect(pickAdds().length).toBe(adds);
      await other.close();
      await appPage.bringToFront();
      await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('main');
    });
  });

  it('runs a voice call: mic grant, offer/answer, state, partial, mute, hang-up', async () => {
    const offscreens = () =>
      sw.evaluate(() => chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT' as never] }).then((c) => c.length));
    const header = panel.locator('.composer'); // the call controls live in the message box

    // 1. Grant from the options page. The fake UI accepts the prompt, and also masks the
    //    no-grant case (spike 1, case B), so the NotAllowedError path is a manual check.
    const options = await ctx.newPage();
    await options.goto(`chrome-extension://${extId}/options.html`);
    const grant = options.getByRole('button', { name: 'Grant microphone' });
    await options.locator('[data-mic]:not([data-mic="unknown"])').waitFor();
    if (await grant.isVisible()) await grant.click();
    await options.locator('[data-mic="granted"]').waitFor();
    await options.close();
    await appPage.bringToFront();

    // 2. Real offer → real answer from a peer connection in the app page.
    daemon.onOffer = (ws, m) => {
      void (async () => {
        const sdp = await appPage.evaluate(async (offer) => {
          // With a media grant the page gathers real host candidates, not mDNS names.
          const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
          const pc = new RTCPeerConnection();
          for (const t of mic.getTracks()) pc.addTrack(t, mic);
          (window as unknown as { __pc: RTCPeerConnection }).__pc = pc;
          await pc.setRemoteDescription({ type: 'offer', sdp: offer });
          await pc.setLocalDescription(await pc.createAnswer());
          await new Promise<void>((r) => {
            if (pc.iceGatheringState === 'complete') return r();
            pc.onicegatheringstatechange = () => pc.iceGatheringState === 'complete' && r();
            setTimeout(r, 2000);
          });
          return pc.localDescription?.sdp ?? '';
        }, m.payload.sdp);
        daemon.send(ws, serverMsg('webrtc.answer', { sdp }));
        daemon.send(ws, serverMsg('call.state', { state: 'listening' }));
      })();
    };
    await header.getByRole('button', { name: 'Start call' }).click();
    await daemon.waitFor('call.start');
    await header.getByText('Connecting…').waitFor();
    const offer = await daemon.waitFor('webrtc.offer');
    expect(offer.payload.sdp).toContain('m=audio');
    expect(offer.payload.sdp).toContain('a=candidate'); // non-trickle: candidates gathered first
    expect(await offscreens()).toBe(1);
    await header.locator('[data-call-state="listening"]').getByText('Listening').waitFor();
    await expect
      // Signalling is done once the answer is applied. ICE never completes in this headless
      // environment (even a same-page loopback stays "new"), so "connected" is a manual check.
      .poll(() => sw.evaluate(() => (globalThis as unknown as { __sidecall: { callLog: { ev: string }[] } }).__sidecall.callLog.some((m) => m.ev === 'answered')), { timeout: 10_000 })
      .toBe(true);
    await panel.locator('.composer [data-call-state]').waitFor();
    // Reaching listening sends the page in view, screenshot uploaded first.
    const callPage = (await daemon.waitFor('call.page')).payload.page;
    expect(callPage.url.startsWith(appOrigin)).toBe(true);
    expect(callPage.screenshot).toMatch(/^[0-9a-f]{32}$/);
    expect(daemon.uploads.get(callPage.screenshot ?? '')).toBeDefined();

    // 3. Live partial transcript, then thinking.
    daemon.broadcast(serverMsg('transcript.partial', { text: 'make the button' }));
    await panel.locator('.partial').getByText('make the button').waitFor();
    daemon.broadcast(serverMsg('call.state', { state: 'thinking' }));
    await header.getByText('Thinking').waitFor();
    daemon.broadcast(serverMsg('call.state', { state: 'listening' }));

    // 4. Mute.
    await header.getByRole('button', { name: 'Mute' }).click();
    expect((await daemon.waitFor('call.mute')).payload.muted).toBe(true);
    await header.getByText('Muted').waitFor();

    // 5. Hang up: call.end, offscreen document closed, back to idle.
    await header.getByRole('button', { name: 'End call' }).click();
    await daemon.waitFor('call.end');
    await expect.poll(offscreens).toBe(0);
    await header.getByRole('button', { name: 'Start call' }).waitFor();
    await expect.poll(() => panel.locator('.partial').count()).toBe(0);
    await appPage.evaluate(() => (window as unknown as { __pc: RTCPeerConnection }).__pc.close());
  });

  it('rebinds on the same socket when the active tab origin changes', async () => {
    const sockets = daemon.origins.length;
    const other = await ctx.newPage();
    await other.goto('data:text/html,<title>Not local</title>');
    await other.bringToFront();
    const nullHello = await daemon.waitFor('hello', (m) => m.payload.origin === null);
    expect(nullHello.payload.token).toBe('tok-test');
    await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('no_origin');
    await panel.getByText('Open your local app (localhost) in this window.').waitFor();

    const before = daemon.of('hello').length;
    await appPage.bringToFront();
    await expect.poll(() => daemon.of('hello').length).toBeGreaterThan(before);
    expect(daemon.of('hello').at(-1)?.payload.origin).toBe(appOrigin);
    await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('main');
    expect(daemon.origins.length).toBe(sockets); // no reconnect
    await other.close();
  });

  it('shows notify as a Chrome notification only when the rail is closed', async () => {
    const getAll = () => sw.evaluate(() => chrome.notifications.getAll().then((n) => Object.keys(n).length));
    daemon.broadcast(serverMsg('notify', { title: 'Red heading done', body: 'Tap to talk', action: 'open' }));
    await sleep(500);
    expect(await getAll()).toBe(0);

    await panel.close();
    await sleep(300);
    daemon.broadcast(serverMsg('notify', { title: 'Red heading done', body: 'Tap to talk', action: 'open' }));
    await expect.poll(getAll).toBe(1);
  });

  it('clicking a notify {action:"call"} notification starts a call', async () => {
    const ids = await sw.evaluate(() => chrome.notifications.getAll().then((n) => Object.keys(n)));
    for (const id of ids) await sw.evaluate((i) => chrome.notifications.clear(i), id);
    daemon.broadcast(serverMsg('notify', { title: 'Done', body: 'Tap to talk', action: 'call' }));
    let id: string | undefined;
    await expect.poll(async () => (id = (await sw.evaluate(() => chrome.notifications.getAll().then((n) => Object.keys(n))))[0])).toBeTruthy();
    expect(id).toMatch(/:call$/);
    const starts = daemon.of('call.start').length;
    // A real click can't be automated: call the registered handler directly.
    await sw.evaluate((i) => (globalThis as unknown as { __sidecall: { onNotificationClicked: (id: string) => void } }).__sidecall.onNotificationClicked(i), id!);
    await expect.poll(() => daemon.of('call.start').length).toBe(starts + 1);
    daemon.broadcast(serverMsg('call.state', { state: 'idle' }));
    await expect
      .poll(() => sw.evaluate(() => chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT' as never] }).then((c) => c.length)))
      .toBe(0);
  });

  it('a reopened panel renders the current state, and forget pairing unpairs', async () => {
    // Open in a background tab so the active tab (and binding) stays the app.
    const opened = ctx.waitForEvent('page');
    await sw.evaluate((url) => chrome.tabs.create({ url, active: false }), `chrome-extension://${extId}/sidepanel.html`);
    panel = await opened;
    await panel.locator('[data-role="agent"]').getByText('Sure — I’ll make the heading red.').waitFor();

    const options = await ctx.newPage();
    await options.goto(`chrome-extension://${extId}/options.html`);
    await options.getByText('Connected and paired').waitFor();
    await options.getByRole('button', { name: 'Forget pairing' }).click();
    await options.getByLabel('Pairing code').waitFor();
    expect(await sw.evaluate(() => chrome.storage.local.get('token'))).toEqual({});
    await expect.poll(() => panel.locator('.rail').getAttribute('data-view')).toBe('pairing');
  });
});
