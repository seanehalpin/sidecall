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
      args: ['--headless=new', '--force-device-scale-factor=2', '--window-size=1280,800', `--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
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
    await panel.locator('header .name').getByText('svelte5-app').waitFor();
    expect(await panel.locator('header [data-status]').getAttribute('data-status')).toBe('connected');
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

    // Queue: running task with live status line, two queued.
    await panel.locator('[data-task-id="t1"]').getByText('Waiting for your approval').waitFor();
    expect(await panel.locator('li.queued').count()).toBe(2);

    // Approve the blocked step.
    const card = panel.locator('[data-approval-id="ap1"]');
    await card.getByText('pnpm add left-pad').waitFor();
    await card.getByRole('button', { name: 'Approve' }).click();
    const respond = await daemon.waitFor('approval.respond');
    expect(respond.payload).toEqual({ id: 'ap1', allow: true });
    await card.waitFor({ state: 'detached' });
    await panel.locator('[data-task-id="t1"]').getByText('editing src/routes/+page.svelte').waitFor();
  });

  it('cancels and reorders queued tasks', async () => {
    await panel.locator('[data-task-id="t3"]').getByRole('button', { name: 'Cancel' }).click();
    expect((await daemon.waitFor('task.cancel')).payload).toEqual({ id: 't3' });

    await panel.locator('[data-task-id="t3"]').dragTo(panel.locator('[data-task-id="t2"]'));
    expect((await daemon.waitFor('task.reorder')).payload).toEqual({ ids: ['t3', 't2'] });
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
