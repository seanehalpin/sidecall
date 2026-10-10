// Milestone 2 acceptance run: the real daemon + the real extension (picker, service
// worker, side panel) + a temp copy of fixtures/svelte5-app with its own dev server.
// It Alt-clicks real elements on the page, checks the pick tray, pins and screenshots,
// then types "make [1] match [2]" and checks the edit. Talks to Claude (roughly $0.50).
//
//   pnpm build && node tests/e2e/m2-acceptance.mjs        (KEEP_TMP=1 keeps the temp dir)
//
// The fixture is copied to a temp dir, so the worker never edits tracked files.

import { execFileSync, spawn } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright';

const ROOT = resolve(import.meta.dirname, '../../..');
const EXT_DIR = join(ROOT, 'extension/.output/chrome-mv3');
const APP_PORT = 5373;
// A port of our own, so a daemon you already have running on 7823 isn't disturbed.
const DAEMON_PORT = 7893;
const results = [];

function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}

function startDaemon(home) {
  const proc = spawn('uv', ['run', '--project', join(ROOT, 'daemon'), 'sidecall', 'start', '--port', String(DAEMON_PORT)], {
    env: { ...process.env, SIDECALL_HOME: home },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let out = '';
  const log = [];
  proc.stderr.on('data', (d) => log.push(d.toString()));
  const code = new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error(`no pairing code:\n${out}`)), 30000);
    proc.stdout.on('data', (d) => {
      out += d.toString();
      const m = out.match(/\b(\d{6})\b/);
      if (m) {
        clearTimeout(t);
        res(m[1]);
      }
    });
  });
  return { proc, code, log };
}

async function stop(proc) {
  if (proc.exitCode !== null) return;
  proc.kill('SIGINT');
  await new Promise((r) => {
    proc.once('exit', r);
    setTimeout(r, 5000);
  });
}

const FINISHED = /^Task (completed|failed|cancelled)/;

async function waitFor(fn, timeout, step = 400) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, step));
  }
  return null;
}

const systemTexts = (panel) => panel.locator('.entry.system').allTextContents();
const finishedCount = async (panel) => (await systemTexts(panel)).filter((t) => FINISHED.test(t)).length;
const waitForNewFinish = (panel, before, timeout) =>
  waitFor(async () => {
    const t = (await systemTexts(panel)).filter((x) => FINISHED.test(x));
    return t.length > before ? t.at(-1) : null;
  }, timeout);

async function send(panel, text) {
  const box = panel.getByLabel('Message');
  await box.fill(text);
  await box.press('Enter');
}

/** Alt+click the centre of a locator, like a user holding Option. */
async function altClick(page, locator) {
  await locator.scrollIntoViewIfNeeded();
  const box = await locator.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.keyboard.down('Alt');
  await page.mouse.move(box.x + box.width / 2 + 1, box.y + box.height / 2 + 1);
  await page.waitForTimeout(150);
  await page.mouse.click(box.x + box.width / 2 + 1, box.y + box.height / 2 + 1);
  await page.keyboard.up('Alt');
}

const tmp = mkdtempSync(join(tmpdir(), 'sidecall-m2-'));
const app = join(tmp, 'app');
const home = join(tmp, 'home');
let daemon;
let vite;
let ctx;

try {
  // --- target repo: a git copy of the svelte fixture with a visibly styled sidebar ---
  cpSync(join(ROOT, 'fixtures/svelte5-app'), app, {
    recursive: true,
    filter: (src) => !/node_modules|\.svelte-kit/.test(src),
  });
  const sidebar = join(app, 'src/routes/components/Sidebar.svelte');
  writeFileSync(
    sidebar,
    readFileSync(sidebar, 'utf8').replace(
      /\ta \{[^}]*\}/,
      '\ta { display: block; padding: 14px 22px; color: #0a7d4b; letter-spacing: .04em; text-decoration: none; border-radius: 6px; }',
    ),
  );
  execFileSync('pnpm', ['install', '--prefer-offline'], { cwd: app, stdio: 'ignore' });
  execFileSync('git', ['init', '-q'], { cwd: app });
  execFileSync('git', ['add', '-A'], { cwd: app });
  execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], { cwd: app });
  vite = spawn('pnpm', ['dev'], { cwd: app, env: { ...process.env, PORT: String(APP_PORT) }, stdio: 'ignore' });

  daemon = startDaemon(home);
  const code = await daemon.code;

  ctx = await chromium.launchPersistentContext(join(tmp, 'profile'), {
    channel: 'chromium',
    headless: true,
    args: ['--headless=new', `--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
  });
  const sw = ctx.serviceWorkers()[0] ?? (await ctx.waitForEvent('serviceworker'));
  const extId = new URL(sw.url()).host;
  await sw.evaluate((port) => chrome.storage.local.set({ daemonPort: port }), DAEMON_PORT);

  const panel = await ctx.newPage();
  await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
  const page = await ctx.newPage();
  for (let i = 0; i < 60; i++) {
    try {
      await page.goto(`http://localhost:${APP_PORT}/`);
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  await page.waitForSelector('[data-testid="card-title"]', { timeout: 30000 });
  await page.waitForTimeout(1500); // let Svelte hydrate: __svelte_meta is attached at hydration
  await panel.bringToFront();
  await panel.getByPlaceholder('123456').fill(code);
  await panel.getByRole('button', { name: 'Pair' }).click();
  await panel.getByLabel('Repo path').fill(app, { timeout: 15000 });
  await panel.getByRole('button', { name: 'Use path' }).click();
  await panel.getByLabel('Message').waitFor({ timeout: 15000 });
  check('paired and repo mapped', await waitFor(() => panel.getByLabel('Message').isEnabled(), 10000));

  // --- 1. pick two elements with Option-click ---------------------------------------
  await page.bringToFront();
  const hasPicker = await page.evaluate(() => !!window.__sidecallPicker);
  check('picker is running on the page', hasPicker);

  // Hover label while Option is held.
  const title3 = page.locator('[data-testid="card-title"]').nth(2);
  const box = await title3.boundingBox();
  await page.mouse.move(box.x + 5, box.y + 5);
  await page.keyboard.down('Alt');
  await page.mouse.move(box.x + 8, box.y + 8);
  await page.waitForTimeout(300);
  const hoverText = await page.evaluate(() => {
    for (const host of document.querySelectorAll('*')) {
      const label = host.shadowRoot?.querySelector('[data-role="hover-label"]');
      if (label) return label.textContent ?? '';
    }
    return '';
  });
  await page.keyboard.up('Alt');
  check('hover label shows component and file:line', /Card/.test(hoverText) && /\.svelte:\d+/.test(hoverText), hoverText.trim().slice(0, 100));

  await altClick(page, title3);
  await panel.locator('.tray .chip').first().waitFor({ timeout: 15000 });
  await altClick(page, page.locator('[data-testid="nav-item"]').nth(1));
  await waitFor(async () => (await panel.locator('.tray .chip').count()) === 2, 15000);
  const chips = await panel.locator('.tray .chip').evaluateAll((els) =>
    els.map((e) => ({ id: e.dataset.pickId, status: e.dataset.status, title: e.getAttribute('title') })),
  );
  check('two chips in the tray', chips.length === 2, JSON.stringify(chips.map((c) => c.id)));
  check('chip 1 tooltip has Card.svelte:8 and repeat info', /Card\.svelte:8/.test(chips[0]?.title ?? '') && /12/.test(chips[0]?.title ?? ''), chips[0]?.title);
  check('chip 2 tooltip has Sidebar.svelte:10', /Sidebar\.svelte:10/.test(chips[1]?.title ?? ''), chips[1]?.title);
  await waitFor(async () => (await panel.locator('.tray .chip[data-status="ready"]').count()) === 2, 20000);
  check('both picks uploaded their screenshots', (await panel.locator('.tray .chip[data-status="ready"]').count()) === 2);

  // Pins are drawn on the page; hovering a chip highlights one.
  const pins = await page.evaluate(() => {
    const out = [];
    for (const host of document.querySelectorAll('*')) {
      for (const el of host.shadowRoot?.querySelectorAll('[data-pin-id]') ?? []) out.push(el.dataset.pinId);
    }
    return out.join(',');
  });
  check('pins drawn on the page', pins.split(',').includes('1') && pins.split(',').includes('2'), pins);
  await page.screenshot({ path: join(tmp, 'page-with-pins.png') });

  const uploads = join(home, 'uploads');
  const uploaded = existsSync(uploads) ? readdirSync(uploads) : [];
  check('four screenshots reached the daemon (crop + viewport per pick)', uploaded.length >= 4, `${uploaded.length} files`);

  // --- 2. "make [1] match [2]" --------------------------------------------------------
  await panel.bringToFront();
  const before = await finishedCount(panel);
  await send(panel, 'make [1] match [2]');
  await panel.locator('.entry.user .pick, .entry.user [data-pick-id], .entry.user .marker').first().waitFor({ timeout: 10000 }).catch(() => {});
  const queued = await waitFor(async () => (await systemTexts(panel)).find((t) => /^Task queued/.test(t)), 90000);
  check('talker dispatched a task', !!queued, queued ?? 'none');
  check('chips dimmed after sending (or consumed once the task dispatched)',
    await waitFor(async () => (await panel.locator('.tray .chip').count()) === 0 || (await panel.locator('.tray .chip[data-dimmed="true"]').count()) === 2, 15000));
  const done = await waitForNewFinish(panel, before, 240000);
  check('task completed', /^Task completed/.test(done ?? ''), (done ?? 'timeout').slice(0, 200));

  const diffFiles = execFileSync('git', ['diff', '--name-only'], { cwd: app }).toString().trim().split('\n').filter(Boolean);
  const diff = execFileSync('git', ['diff'], { cwd: app }).toString();
  check('edit landed in Card.svelte (the shared template)', diffFiles.includes('src/routes/components/Card.svelte'), diffFiles.join(', '));
  check('CardList.svelte and +page.svelte untouched', !diffFiles.some((f) => /CardList|\+page/.test(f)), diffFiles.join(', '));
  check('new style matches the sidebar link', /0a7d4b|14px 22px|letter-spacing/i.test(diff), (diff.match(/^\+.*(0a7d4b|14px 22px|letter-spacing).*$/im) ?? [''])[0].slice(0, 120));
  check('consumed picks left the tray', await waitFor(async () => (await panel.locator('.tray .chip').count()) === 0, 20000));

  // --- 3. a pick on a repeated element: the edit lands in the template ------------------
  await page.bringToFront();
  await page.waitForTimeout(1500);
  await altClick(page, page.locator('article.card button').nth(3));
  await panel.locator('.tray .chip').first().waitFor({ timeout: 15000 });
  await panel.bringToFront();
  const before2 = await finishedCount(panel);
  await send(panel, 'make [1] fully rounded like a pill, with a blue background and white text');
  const done2 = await waitForNewFinish(panel, before2, 240000);
  check('second task completed', /^Task completed/.test(done2 ?? ''), (done2 ?? 'timeout').slice(0, 160));
  const files2 = execFileSync('git', ['diff', '--name-only'], { cwd: app }).toString().trim().split('\n');
  const cardDiff = execFileSync('git', ['diff', '--', 'src/routes/components/Card.svelte'], { cwd: app }).toString();
  check('button edit is in the Card template/styles (all 12 cards)', /button/.test(cardDiff) && !files2.some((f) => /CardList|\+page/.test(f)), files2.join(', '));
  await page.bringToFront();
  await page.waitForTimeout(2000);
  const radii = await page.evaluate(() => [...document.querySelectorAll('article.card button')].map((b) => getComputedStyle(b).borderRadius));
  check('page hot-reloaded: every card button changed', radii.length === 12 && new Set(radii).size === 1 && !/^0px$/.test(radii[0]), `${radii.length} buttons, radius ${radii[0]}`);
} catch (err) {
  check('run completed without errors', false, String(err?.stack ?? err).slice(0, 500));
  for (const pg of ctx?.pages() ?? []) {
    if (pg.url().includes('sidepanel')) console.log('PANEL TEXT:\n' + (await pg.locator('body').innerText().catch(() => '')).slice(0, 1800));
  }
  if (daemon) console.log('DAEMON LOG (tail):\n' + daemon.log.join('').slice(-2000));
} finally {
  await ctx?.close().catch(() => {});
  if (daemon) await stop(daemon.proc);
  if (vite) vite.kill('SIGINT');
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (process.env.KEEP_TMP) console.log(`kept ${tmp}`);
  else rmSync(tmp, { recursive: true, force: true });
  process.exit(failed.length ? 1 : 0);
}
