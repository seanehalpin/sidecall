// Automated verification for the picker spike.
// Usage: node test.mjs [--headed] [--only=svelte5-app,react19-app] [--no-devtools] [--no-hmr]
// Ports default to 5273/5274/5275 (5173-5175 + 100) so they don't clash with other dev servers;
// override with PORT_BASE=5173 to use the fixtures' default ports.
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const args = process.argv.slice(2);
const headed = args.includes('--headed');
const only = args.find((a) => a.startsWith('--only='))?.slice(7).split(',');
const portBase = Number(process.env.PORT_BASE ?? 5273);
const devtoolsDir = path.join(here, '.cache/react-devtools');
const haveDevtools = fs.existsSync(path.join(devtoolsDir, 'manifest.json')) && !args.includes('--no-devtools');
const doHmr = !args.includes('--no-hmr');

const fixtures = [
  {
    name: 'svelte5-app',
    port: portBase,
    base: 'src/routes/components',
    ext: 'svelte',
    hmrPrefix: '<!-- hmr-probe -->\n',
  },
  { name: 'react18-app', port: portBase + 1, base: 'src/components', ext: 'tsx', hmrPrefix: '// hmr-probe\n' },
  { name: 'react19-app', port: portBase + 2, base: 'src/components', ext: 'tsx', hmrPrefix: '// hmr-probe\n' },
].filter((f) => !only || only.includes(f.name));

const targets = [
  { label: 'card title (3rd)', selector: '[data-testid=card-title]', nth: 2, file: 'Card', pattern: /<h2\b/, repeat: { count: 12, index: 2 } },
  { label: 'sidebar item (2nd)', selector: '[data-testid=nav-item]', nth: 1, file: 'Sidebar', pattern: /<a\b/, repeat: { count: 5, index: 1 } },
  { label: 'form submit', selector: '[data-testid=submit]', nth: 0, file: 'Form', pattern: /<button type="submit"/, repeat: { count: 1, index: 0 } },
  { label: 'card root (4th)', selector: '.card', nth: 3, position: { x: 3, y: 3 }, file: 'Card', pattern: /<article\b/, repeat: { count: 12, index: 3 } },
  { label: 'card button (1st)', selector: '.card button', nth: 0, file: 'Card', pattern: /<button\b/, repeat: { count: 12, index: 0 } },
];

function expectedFor(fx, t) {
  const rel = `${fx.base}/${t.file}.${fx.ext}`;
  const lines = fs.readFileSync(path.join(root, 'fixtures', fx.name, rel), 'utf8').split('\n');
  const idx = lines.findIndex((l) => t.pattern.test(l));
  return { rel, line: idx + 1 };
}

async function waitForHttp(url, ms = 30000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`timeout waiting for ${url}`);
}

function startServer(fx) {
  const proc = spawn('pnpm', ['dev'], {
    cwd: path.join(root, 'fixtures', fx.name),
    env: { ...process.env, PORT: String(fx.port), BROWSER: 'none' },
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: true,
  });
  let log = '';
  proc.stdout.on('data', (d) => (log += d));
  proc.stderr.on('data', (d) => (log += d));
  return { proc, log: () => log, stop: () => { try { process.kill(-proc.pid, 'SIGTERM'); } catch {} } };
}

async function pick(page, t) {
  const before = await page.evaluate(() => window.__sidecallPicks?.length ?? 0);
  await page.locator(t.selector).nth(t.nth).click({ modifiers: ['Alt'], position: t.position });
  await page.waitForFunction((n) => (window.__sidecallPicks?.length ?? 0) > n, before, { timeout: 15000 });
  return page.evaluate(() => window.__sidecallLastPick);
}

function compare(fx, t, result, expected) {
  const src = result?.framework?.source;
  const fileOk = !!src && src.file.replace(/\?.*$/, '').endsWith(expected.rel);
  const lineOk = !!src && src.line === expected.line;
  const rep = result?.repeat;
  const repeatOk = !!rep && rep.count === t.repeat.count && rep.index === t.repeat.index;
  return { fileOk, lineOk, repeatOk };
}

async function launch(extDirs) {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sidecall-picker-'));
  return chromium.launchPersistentContext(userDataDir, {
    channel: 'chromium',
    headless: !headed,
    args: [...(headed ? [] : ['--headless=new']), `--disable-extensions-except=${extDirs.join(',')}`, `--load-extension=${extDirs.join(',')}`],
  });
}

const rows = [];
const notes = [];

async function runPass(passName, extDirs) {
  for (const fx of fixtures) {
    const server = startServer(fx);
    const url = `http://localhost:${fx.port}/`;
    let ctx;
    try {
      await waitForHttp(url);
      ctx = await launch(extDirs);
      // React DevTools registers its MAIN-world hook via chrome.scripting.registerContentScripts
      // from its service worker; give that a moment on a fresh profile.
      if (extDirs.length > 1) await new Promise((r) => setTimeout(r, 1500));
      const page = await ctx.newPage();
      const consoleErrors = [];
      page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()));
      page.on('pageerror', (e) => consoleErrors.push(String(e)));
      await page.goto(url);
      await page.waitForSelector('[data-testid=card-title]');
      await page.waitForTimeout(300);
      const boot = await page.evaluate(() => {
        const h = window.__REACT_DEVTOOLS_GLOBAL_HOOK__;
        return {
          boot: window.__sidecallBoot,
          hook: h
            ? {
                isBippy: h._isBippyHook === true,
                renderers: h.renderers ? h.renderers.size : null,
                rendererInterfaces: h.rendererInterfaces ? h.rendererInterfaces.size : null,
                backends: h.backends ? h.backends.size : null,
                hasGetFiberRoots: typeof h.getFiberRoots === 'function',
                instrumentationSource: h._instrumentationSource ?? null,
                injectIsDevtools: (() => { try { return Function.prototype.toString.call(h.inject).length; } catch { return null; } })(),
              }
            : null,
        };
      });
      notes.push({ pass: passName, fixture: fx.name, ...boot, consoleErrors: consoleErrors.slice(0, 5) });

      for (const t of targets) {
        const expected = expectedFor(fx, t);
        const result = await pick(page, t);
        rows.push({ pass: passName, fixture: fx.name, target: t.label, expected, result, ...compare(fx, t, result, expected) });
      }
      // alt+click must not trigger page behaviour
      const sideEffects = await page.evaluate(() => ({
        invited: !!document.querySelector('.invite-form p'),
        firstCardButton: document.querySelector('.card button')?.textContent?.trim(),
        activeNav: document.querySelector('.sidebar a.active')?.textContent?.trim(),
        url: location.href,
      }));
      notes.push({ pass: passName, fixture: fx.name, sideEffects });

      if (doHmr && passName === 'picker only') {
        const t = targets[0];
        const hmrTargets = [targets[0], targets[3]];
        const fileAbs = path.join(root, 'fixtures', fx.name, `${fx.base}/${t.file}.${fx.ext}`);
        const original = fs.readFileSync(fileAbs, 'utf8');
        try {
          fs.writeFileSync(fileAbs, fx.hmrPrefix + original);
          await page.waitForTimeout(1500); // let HMR apply
          const reloaded = await page.evaluate(() => performance.getEntriesByType('navigation').length);
          for (const ht of hmrTargets) {
            const expected = expectedFor(fx, ht);
            const result = await pick(page, ht);
            rows.push({ pass: passName + ' +HMR', fixture: fx.name, target: ht.label, expected, result, ...compare(fx, ht, result, expected) });
          }
          notes.push({ pass: 'hmr', fixture: fx.name, navEntries: reloaded });
        } finally {
          fs.writeFileSync(fileAbs, original);
          await page.waitForTimeout(800);
        }
      }
    } catch (err) {
      rows.push({ pass: passName, fixture: fx.name, target: 'ERROR', error: String(err), log: server.log().slice(-2000) });
    } finally {
      await ctx?.close().catch(() => {});
      server.stop();
      await new Promise((r) => setTimeout(r, 500));
    }
  }
}

const pickerDir = path.join(here, 'ext');
await runPass('picker only', [pickerDir]);
if (haveDevtools) {
  await runPass('picker + RDT (picker first)', [pickerDir, devtoolsDir]);
  await runPass('picker + RDT (RDT first)', [devtoolsDir, pickerDir]);
  await runPass('NO-SHIM picker + RDT', [path.join(here, 'ext-noshim'), devtoolsDir]);
} else {
  console.log('(React DevTools not found in .cache/react-devtools — skipping coexistence passes)');
}

// ---- report
const pad = (s, n) => String(s ?? '').padEnd(n).slice(0, n);
console.log('\n' + [pad('pass', 28), pad('fixture', 12), pad('target', 19), pad('expected', 28), pad('actual', 44), pad('prec', 9), pad('repeat', 7), 'ok'].join(' '));
for (const r of rows) {
  if (r.error) {
    console.log(pad(r.pass, 28), pad(r.fixture, 12), 'ERROR', r.error, '\n', r.log);
    continue;
  }
  const s = r.result?.framework?.source;
  const actual = s ? `${s.file.replace(/^.*?(src\/)/, '$1')}:${s.line}:${s.column}` : '(none)';
  const rep = r.result?.repeat ? `${r.result.repeat.index}/${r.result.repeat.count}` : '-';
  const stale = r.result?.debug?.stale === true;
  const ok = r.fileOk && r.lineOk ? (r.repeatOk ? 'PASS' : 'PASS (repeat ✗)') : r.fileOk ? (stale ? 'STALE→component (expected)' : 'FILE ONLY') : 'FAIL';
  console.log([pad(r.pass, 28), pad(r.fixture, 12), pad(r.target, 19), pad(`${r.expected.rel.replace(/^.*\//, '')}:${r.expected.line}`, 28), pad(actual, 44), pad(s?.precision, 9), pad(rep, 7), ok].join(' '));
}
fs.writeFileSync(path.join(here, 'results.json'), JSON.stringify({ rows, notes }, null, 2));
console.log('\nnotes:');
for (const n of notes) console.log(JSON.stringify(n));
console.log('\nfull results in results.json');
// A stale React 19 stack (fiber reused across HMR, no fresh element reachable) is expected to
// degrade to component precision with the right file; anything else is a failure.
const failed = rows.some((r) => r.error || !(r.fileOk && (r.lineOk || (r.result?.debug?.stale && r.result.framework.source.precision === 'component'))));
process.exit(failed ? 1 : 0);
