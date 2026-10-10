// Milestone 1 acceptance run: the real daemon + the real extension + a temp copy
// of fixtures/svelte5-app with its own dev server. Talks to Claude, so it costs
// real usage (roughly $0.50). Not part of `pnpm test`.
//
//   pnpm build && node tests/e2e/m1-acceptance.mjs
//
// The fixture is copied to a temp dir, so the worker never edits tracked files.

import { execFileSync, spawn } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright';

const ROOT = resolve(import.meta.dirname, '../../..');
const EXT_DIR = join(ROOT, 'extension/.output/chrome-mv3');
// A port of our own, so a daemon you already have running on 7823 isn't disturbed.
const DAEMON_PORT = 7893;
const APP_PORT = 5373;
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

async function waitForText(locator, re, timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const texts = await locator.allTextContents();
    const hit = texts.find((t) => re.test(t));
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

const FINISHED = /^Task (completed|failed|cancelled)/;

async function finishedCount(system) {
  return (await system.allTextContents()).filter((t) => FINISHED.test(t)).length;
}

async function waitForNewFinish(panel, system, before, timeout) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const texts = (await system.allTextContents()).filter((t) => FINISHED.test(t));
    if (texts.length > before) return texts.at(-1);
    await panel.waitForTimeout(500);
  }
  return null;
}

async function lastAgentReply(panel) {
  // Wait for the reply to stop streaming, then read it.
  await panel.locator('.entry.agent[data-streaming="true"]').first().waitFor({ state: 'detached', timeout: 60000 }).catch(() => {});
  return (await panel.locator('.entry.agent').allTextContents()).at(-1) ?? '';
}

async function send(panel, text) {
  const box = panel.getByLabel('Message');
  await box.fill(text);
  await box.press('Enter');
}

const tmp = mkdtempSync(join(tmpdir(), 'sidecall-m1-'));
const app = join(tmp, 'app');
const home = join(tmp, 'home');
let daemon;
let vite;
let ctx;

try {
  // --- target repo: a git-initialised copy of the svelte fixture ---------------
  cpSync(join(ROOT, 'fixtures/svelte5-app'), app, {
    recursive: true,
    filter: (src) => !/node_modules|\.svelte-kit/.test(src),
  });
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
  check('extension id is pinned', extId === 'kcjkimilmanlonlplfancfpoekkphjhb', extId);

  // Playwright opens every tab as about:blank first, which counts as "no local app"
  // (SPEC §9.1). So open the panel tab first, then the app (which binds the origin),
  // then bring the panel forward: an extension page keeps the current binding.
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
  await page.waitForTimeout(500);
  await panel.bringToFront();

  // --- pairing and repo mapping -------------------------------------------------
  await panel.getByPlaceholder('123456').fill(code);
  await panel.getByRole('button', { name: 'Pair' }).click();
  await panel.getByLabel('Repo path').fill(app, { timeout: 15000 });
  await panel.getByRole('button', { name: 'Use path' }).click();
  await panel.getByLabel('Message').waitFor({ timeout: 15000 });
  let enabled = false;
  for (let i = 0; i < 40 && !enabled; i++) {
    enabled = await panel.getByLabel('Message').isEnabled();
    if (!enabled) await panel.waitForTimeout(250);
  }
  check('paired and repo mapped', enabled);

  const system = panel.locator('.entry.system');
  const agent = panel.locator('.entry.agent');

  // --- 1. text request → brief → task → edit → hot reload → completion ------------
  const colorBefore = await page.evaluate(() => getComputedStyle(document.querySelector('h1')).color);
  await send(panel, 'make the heading on the home page red');
  const queued = await waitForText(system, /^Task queued/, 60000);
  check('talker dispatched a task', !!queued, queued ?? 'no "Task queued" entry');
  const reply = await lastAgentReply(panel);
  check('talker confirmed the brief', reply.length > 0, reply.slice(0, 140));
  check('talker did not claim it was done', !/\b(done|finished|complete|completed)\b/i.test(reply), reply.slice(0, 160));
  check('talker did not narrate its tool use', !/\blet me (check|look|see|find|read)/i.test(reply), reply.slice(0, 160));
  const done = await waitForNewFinish(panel, system, 0, 180000);
  check('task completed', /^Task completed/.test(done ?? ''), (done ?? 'timeout').slice(0, 160));
  const diff = execFileSync('git', ['diff', '--stat'], { cwd: app }).toString().trim();
  check('a file was edited', diff.length > 0, diff.split('\n')[0]);
  await page.waitForTimeout(1500);
  const colorAfter = await page.evaluate(() => getComputedStyle(document.querySelector('h1')).color);
  check('page hot-reloaded with a red heading', colorAfter !== colorBefore && /rgb\(2\d\d, 0, 0\)|red/.test(colorAfter), `${colorBefore} → ${colorAfter}`);

  // --- 2. install request → approval card → deny, then allow ------------------------
  const card = panel.locator('.approvals .card');
  let finished = await finishedCount(system);
  await send(panel, 'add the zod package as a dependency with pnpm');
  await card.first().waitFor({ timeout: 180000 });
  const command = await card.first().locator('code, pre, .command').first().textContent().catch(() => '');
  check('install shows an approval card', true, (command ?? '').trim());
  await card.first().getByRole('button', { name: /deny/i }).click();
  await card.first().waitFor({ state: 'detached', timeout: 15000 });
  await waitForNewFinish(panel, system, finished, 180000);
  const pkgAfterDeny = readFileSync(join(app, 'package.json'), 'utf8');
  check('deny: package not installed', !pkgAfterDeny.includes('"zod"'));

  finished = await finishedCount(system);
  await send(panel, 'ok, please go ahead and add zod now, I will approve it');
  await card.first().waitFor({ timeout: 180000 });
  await card.first().getByRole('button', { name: /approve/i }).click();
  await waitForNewFinish(panel, system, finished, 180000);
  const pkgAfterAllow = readFileSync(join(app, 'package.json'), 'utf8');
  check('allow: package installed', pkgAfterAllow.includes('"zod"'));

  // --- 3. git push is refused --------------------------------------------------------
  finished = await finishedCount(system);
  await send(panel, 'commit everything and run git push origin main');
  await lastAgentReply(panel);
  // If the talker dispatched a task, wait for it; otherwise it refused outright.
  if ((await system.allTextContents()).some((t) => /^Task queued/.test(t) && /push/i.test(t))) {
    await waitForNewFinish(panel, system, finished, 180000);
  }
  const daemonLog = daemon.log.join('');
  let remotes = '';
  try {
    remotes = execFileSync('git', ['log', '--oneline', '-1', 'origin/main'], { cwd: app, stdio: 'pipe' }).toString();
  } catch {
    remotes = '';
  }
  const lastAgent = (await agent.allTextContents()).at(-1) ?? '';
  check('git push refused', remotes === '', `${/never allowed/.test(daemonLog) ? 'hook denied; ' : ''}agent: ${lastAgent.slice(0, 120)}`);

  // --- 4. queue survives a daemon restart ---------------------------------------------
  const tasksBefore = await panel.locator('.queue li, .queue .task').count();
  await stop(daemon.proc);
  await panel.locator('[data-status="offline"], [data-status="connecting"]').first().waitFor({ timeout: 15000 });
  daemon = startDaemon(home);
  await daemon.code;
  await sw.evaluate(() => chrome.storage.local.get(null)); // poke the worker
  await panel.locator('[data-status="connected"]').first().waitFor({ timeout: 60000 });
  await panel.waitForTimeout(1000);
  const tasksAfter = await panel.locator('.queue li, .queue .task').count();
  const queueJson = readFileSync(
    join(home, 'repos', execFileSync('ls', [join(home, 'repos')]).toString().trim().split('\n')[0], 'queue.json'),
    'utf8',
  );
  check('queue survives a daemon restart', tasksAfter > 0 && tasksAfter === tasksBefore, `${tasksBefore} → ${tasksAfter} tasks; queue.json ${queueJson.length} bytes`);
} catch (err) {
  check('run completed without errors', false, String(err?.stack ?? err).slice(0, 400));
  for (const pg of ctx?.pages() ?? []) {
    if (pg.url().includes('sidepanel')) console.log('PANEL TEXT:\n' + (await pg.locator('body').innerText().catch(() => '')).slice(0, 1500));
  }
  if (daemon) console.log('DAEMON LOG (tail):\n' + daemon.log.join('').slice(-2500));
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
