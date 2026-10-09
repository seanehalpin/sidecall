// Live page-context check: the real daemon + real extension + a temp copy of
// fixtures/svelte5-app. One or two haiku talker turns plus one haiku worker task,
// so it costs a little real usage. Not part of `pnpm test`.
//
//   pnpm build && node tests/e2e/page-context-live.mjs
//
// Checks that a typed turn carries the page (URL, route, title, screenshot), that
// the dispatched task stores it, and that the brief mentions the page.

import { execFileSync, spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright';

const ROOT = resolve(import.meta.dirname, '../../..');
const EXT_DIR = join(ROOT, 'extension/.output/chrome-mv3');
const DAEMON_PORT = 7891;
const APP_PORT = 5374;
const ORIGIN = `http://localhost:${APP_PORT}`;
const results = [];

function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}

const tmp = mkdtempSync(join(tmpdir(), 'sidecall-page-'));
const app = join(tmp, 'app');
const home = join(tmp, 'home');
const token = randomBytes(24).toString('hex');
let daemon;
let vite;
let ctx;
const daemonLog = [];

function tasks() {
  const repos = join(home, 'repos');
  if (!existsSync(repos)) return [];
  const [dir] = readdirSync(repos);
  const file = dir && join(repos, dir, 'queue.json');
  if (!file || !existsSync(file)) return [];
  const data = JSON.parse(readFileSync(file, 'utf8'));
  return [...(data.active ?? data.tasks ?? []), ...(data.recent ?? [])];
}

async function until(fn, timeout, step = 500) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const v = await fn();
    if (v) return v;
    await new Promise((r) => setTimeout(r, step));
  }
  return null;
}

try {
  cpSync(join(ROOT, 'fixtures/svelte5-app'), app, {
    recursive: true,
    filter: (src) => !/node_modules|\.svelte-kit/.test(src),
  });
  execFileSync('pnpm', ['install', '--prefer-offline'], { cwd: app, stdio: 'ignore' });
  execFileSync('git', ['init', '-q'], { cwd: app });
  execFileSync('git', ['add', '-A'], { cwd: app });
  execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], { cwd: app });
  vite = spawn('pnpm', ['dev'], { cwd: app, env: { ...process.env, PORT: String(APP_PORT) }, stdio: 'ignore' });

  // Pre-seeded config: known token, repo already mapped, haiku for both agents.
  mkdirSync(home, { recursive: true });
  writeFileSync(
    join(home, 'config.toml'),
    [
      `port = ${DAEMON_PORT}`,
      `token = "${token}"`,
      'extension_id = "kcjkimilmanlonlplfancfpoekkphjhb"',
      'talker_model = "haiku"',
      'worker_model = "haiku"',
      '[repos]',
      `"${ORIGIN}" = "${app}"`,
      '',
    ].join('\n'),
  );
  daemon = spawn('uv', ['run', '--project', join(ROOT, 'daemon'), 'sidecall', 'start'], {
    env: { ...process.env, SIDECALL_HOME: home },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  daemon.stderr.on('data', (d) => daemonLog.push(d.toString()));
  await until(() => fetch(`http://127.0.0.1:${DAEMON_PORT}/health`).then((r) => r.ok, () => false), 30000);

  ctx = await chromium.launchPersistentContext(join(tmp, 'profile'), {
    channel: 'chromium',
    headless: true,
    viewport: null,
    args: ['--headless=new', '--window-size=1440,900', `--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
  });
  const sw = ctx.serviceWorkers()[0] ?? (await ctx.waitForEvent('serviceworker'));
  const extId = new URL(sw.url()).host;
  await sw.evaluate(([port, t]) => chrome.storage.local.set({ daemonPort: port, token: t }), [DAEMON_PORT, token]);

  // The panel lives in a background tab; the app is the active tab (like the real side panel).
  const panel = await ctx.newPage();
  await panel.goto(`chrome-extension://${extId}/sidepanel.html`);
  const page = await ctx.newPage();
  await until(() => page.goto(`${ORIGIN}/?view=cards`).then(() => true, () => false), 30000);
  await page.bringToFront();
  const box = panel.getByLabel('Message');
  check('rail bound to the app', !!(await until(() => box.isEnabled(), 30000)));

  const send = async (text) => {
    await box.fill(text);
    await box.press('Enter');
  };
  const agentCount = () => panel.locator('.entry.agent[data-streaming="false"]').count();

  let before = await agentCount();
  await send('make the card titles purple');
  await until(async () => (await agentCount()) > before || tasks().length > 0, 90000);
  let reply = (await panel.locator('.entry.agent').allTextContents()).at(-1) ?? '';
  console.log(`talker: ${reply}`);
  if (tasks().length === 0 && /page|everywhere/i.test(reply)) {
    check('talker asked page-or-everywhere', true, reply.slice(0, 140));
    before = await agentCount();
    await send('just on this page');
    await until(() => tasks().length > 0, 90000);
    reply = (await panel.locator('.entry.agent').allTextContents()).at(-1) ?? '';
    console.log(`talker: ${reply}`);
  }
  const [task] = tasks();
  check('a task was dispatched', !!task, task?.title ?? 'none');
  if (task) {
    const p = task.page;
    check('task.page has url/route/title', p?.url === `${ORIGIN}/?view=cards` && p?.route === '/?view=cards' && typeof p?.title === 'string', JSON.stringify(p ?? null).slice(0, 200));
    const shot = p?.screenshot && join(home, 'uploads', `${p.screenshot}.jpg`);
    check('screenshot uploaded', !!shot && existsSync(shot) && readFileSync(shot).length > 1000, shot ?? 'no ref');
    const brief = JSON.stringify(task.brief);
    // The worker gets task.page regardless; this checks the talker also wrote it into the brief.
    const literal = brief.includes('localhost:5374') || brief.includes('view=cards');
    check('brief mentions the page', literal || /\bpage\b/i.test(brief), literal ? 'url/route quoted' : 'page named, url/route not quoted');
    console.log(`brief: ${brief}`);
    const done = await until(() => tasks().find((t) => t.id === task.id && ['completed', 'failed', 'cancelled'].includes(t.status)), 240000, 1000);
    check('worker finished', done?.status === 'completed', `${done?.status ?? 'timeout'}: ${(done?.summary ?? done?.error ?? '').slice(0, 160)}`);
  }
  const costs = [...daemonLog.join('').matchAll(/cost=\$([\d.]+)/g)].map((m) => Number(m[1]));
  console.log(`cost lines: ${costs.join(', ')}  (talker costs are cumulative per session)`);
} catch (err) {
  check('run completed without errors', false, String(err?.stack ?? err).slice(0, 400));
  console.log('DAEMON LOG (tail):\n' + daemonLog.join('').slice(-2500));
} finally {
  await ctx?.close().catch(() => {});
  if (daemon && daemon.exitCode === null) {
    daemon.kill('SIGINT');
    await new Promise((r) => {
      daemon.once('exit', r);
      setTimeout(r, 5000);
    });
  }
  if (vite) vite.kill('SIGINT');
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (process.env.KEEP_TMP) console.log(`kept ${tmp}`);
  else rmSync(tmp, { recursive: true, force: true });
  process.exit(failed.length ? 1 : 0);
}
