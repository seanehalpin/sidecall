// Picker e2e: the real extension (MAIN-world picker + bridge) loaded into headless Chromium,
// driven against temp copies of the three fixtures. The service worker is NOT involved: the
// harness plays its role by listening for the picker's window.postMessage envelopes and
// answering with ext-to-page envelopes (nonce taken from the handshake).
//
//   pnpm build && node tests/e2e/picker-live.mjs [--only=svelte5-app]
//
// Dev servers run on PORT_BASE.. (default 5473); fixtures are copied to a temp dir and never touched.

import { execFileSync, spawn } from 'node:child_process';
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { chromium } from 'playwright';

const ROOT = resolve(import.meta.dirname, '../../..');
const EXT_DIR = join(ROOT, 'extension/.output/chrome-mv3');
const PORT_BASE = Number(process.env.PORT_BASE ?? 5473);
const only = process.argv.find((a) => a.startsWith('--only='))?.slice(7);

const FIXTURES = [
  {
    name: 'svelte5-app', framework: 'svelte', version: /^5$/,
    card: 'src/routes/components/Card.svelte', sidebar: 'src/routes/components/Sidebar.svelte', form: 'src/routes/components/Form.svelte',
    hmrPrefix: '<!-- hmr -->\n',
  },
  {
    name: 'react18-app', framework: 'react', version: /^18\./,
    card: 'src/components/Card.tsx', sidebar: 'src/components/Sidebar.tsx', form: 'src/components/Form.tsx',
    hmrPrefix: '// hmr\n',
  },
  {
    name: 'react19-app', framework: 'react', version: /^19\./,
    card: 'src/components/Card.tsx', sidebar: 'src/components/Sidebar.tsx', form: 'src/components/Form.tsx',
    hmrPrefix: '// hmr\n',
  },
];

const results = [];
const table = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, timeout = 8000, step = 100) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const v = await fn();
    if (v) return v;
    await sleep(step);
  }
  return null;
}

const lineOf = (file, re) => {
  const lines = readFileSync(file, 'utf8').split('\n');
  const i = lines.findIndex((l) => re.test(l));
  return i < 0 ? -1 : i + 1;
};

// ------------------------------------------------------------------ harness (runs in the page)
const INIT = () => {
  window.__msgs = [];
  window.__nonce = null;
  window.addEventListener('message', (e) => {
    const d = e.data;
    if (e.source !== window || !d || d.channel !== 'sidecall-picker') return;
    if (d.dir === 'handshake') window.__nonce = window.__nonce ?? d.nonce;
    else if (d.dir === 'page-to-ext') {
      window.__nonce = window.__nonce ?? d.nonce;
      window.__msgs.push(d.msg);
    }
  });
  window.__reply = (msg) =>
    window.postMessage({ channel: 'sidecall-picker', nonce: window.__nonce, dir: 'ext-to-page', msg }, '*');
};

const take = (page, t, timeout = 8000, pred = null) =>
  until(
    () =>
      page.evaluate(
        ([T, P]) => {
          const f = P ? new Function('m', `return (${P})(m)`) : () => true;
          const i = window.__msgs.findIndex((m) => m.t === T && f(m));
          return i < 0 ? null : window.__msgs.splice(i, 1)[0];
        },
        [t, pred],
      ),
    timeout,
  );
const reply = (page, msg) => page.evaluate((m) => window.__reply(m), msg);
const pinsSnapshot = (page) => page.evaluate(() => window.__sidecallPicker?.pins.snapshot());
const pinNode = (page, id) =>
  page.evaluate((i) => {
    const n = document.getElementById('sidecall-picker-root')?.shadowRoot?.querySelector(`[data-pin-id="${i}"]`);
    return n ? { state: n.dataset.state, x: Number(n.dataset.x), y: Number(n.dataset.y), text: n.textContent, display: n.style.display } : null;
  }, id);

async function altClick(page, x, y) {
  await page.keyboard.down('Alt');
  await page.mouse.move(x - 3, y - 3);
  await page.mouse.move(x, y, { steps: 3 });
  await page.mouse.click(x, y);
  await page.keyboard.up('Alt');
}
async function altDrag(page, x0, y0, x1, y1) {
  await page.keyboard.down('Alt');
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  await page.mouse.move(x1, y1, { steps: 8 });
  await page.mouse.up();
  await page.keyboard.up('Alt');
}
async function box(page, sel, n = 0) {
  const b = await page.locator(sel).nth(n).boundingBox();
  if (!b) throw new Error(`no box for ${sel}[${n}]`);
  return b;
}
const center = (b) => [b.x + b.width / 2, b.y + b.height / 2];

let nextPin = 1;
async function pickAndPin(page, doClick) {
  await doClick();
  const pick = await take(page, 'pick');
  if (!pick) return null;
  const id = nextPin++;
  await reply(page, {
    t: 'pin-add',
    localId: pick.localId,
    pin: { id, anchor: pick.anchor, label: 'x', dimmed: false },
  });
  return { pick, id };
}

// ------------------------------------------------------------------ per-fixture run
async function runFixture(fx, idx, ctx, tmp) {
  const label = (s) => `${fx.name}: ${s}`;
  const app = join(tmp, fx.name);
  cpSync(join(ROOT, 'fixtures', fx.name), app, {
    recursive: true,
    filter: (src) => !/node_modules|\.svelte-kit|\.vite/.test(src),
  });
  execFileSync('pnpm', ['install', '--prefer-offline'], { cwd: app, stdio: 'ignore' });
  const port = PORT_BASE + idx;
  const vite = spawn('pnpm', ['dev'], { cwd: app, env: { ...process.env, PORT: String(port) }, stdio: 'ignore' });
  const page = await ctx.newPage();
  const problems = [];
  let hotUpdates = 0;
  page.on('pageerror', (e) => !e.message.includes('e2e') && problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    const t = m.text();
    if (t.includes('[sidecall]') && m.type() === 'warning') problems.push(t);
    if (t.includes('hot updated')) hotUpdates++;
  });
  await page.addInitScript(INIT);
  try {
    if (!(await until(async () => (await fetch(`http://localhost:${port}/`).catch(() => null))?.ok, 40000, 300))) {
      check(label('dev server up'), false);
      return;
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`http://localhost:${port}/`);
    await page.locator('.card').first().waitFor();
    // Make the page scrollable (pins must move, ↑ must not scroll).
    await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<div id="e2e-spacer" style="height:3000px"></div>'));
    const ready = await take(page, 'ready');
    check(label("posts 'ready' with the url"), !!ready && ready.url.startsWith(`http://localhost:${port}`));
    nextPin = 1;

    // ---- plain click still works (no interception without Alt). Also waits for hydration,
    // which is when Svelte attaches __svelte_meta.
    const star = page.locator('.card button').first();
    const toggled = await until(async () => {
      await star.click();
      return (await star.innerText()) === 'Starred';
    }, 15000, 300);
    check(label('plain click not intercepted (Star toggles)'), !!toggled);
    await star.click();
    await until(async () => (await star.innerText()) === 'Star', 3000);

    // ---- expected source lines from the fixture
    const cardFile = join(app, fx.card);
    const exp = {
      title: lineOf(cardFile, /<h2[^>]*card-title/),
      root: lineOf(cardFile, /<article/),
      side: lineOf(join(app, fx.sidebar), /^\s*<a\b/),
      form: lineOf(join(app, fx.form), /<button[^>]*type="submit"/),
    };
    const targets = [
      { key: 'card title (3rd)', file: fx.card, line: exp.title, sel: '[data-testid=card-title]', n: 2, chain0: 'Card', repeat: { count: 12, index: 2 }, tag: 'h2' },
      { key: 'sidebar item (2nd)', file: fx.sidebar, line: exp.side, sel: '[data-testid=nav-item]', n: 1, chain0: 'Sidebar', repeat: { count: 5, index: 1 }, tag: 'a' },
      { key: 'form submit', file: fx.form, line: exp.form, sel: '[data-testid=submit]', n: 0, chain0: 'Form', repeat: { count: 1, index: 0 }, tag: 'button' },
      { key: 'card root (4th)', file: fx.card, line: exp.root, sel: '.card', n: 3, chain0: 'Card', repeat: { count: 12, index: 3 }, tag: 'article', corner: true },
    ];
    const urlBefore = page.url();
    const picks = {};
    for (const t of targets) {
      const b = await box(page, t.sel, t.n);
      const [cx, cy] = t.corner ? [b.x + 4, b.y + 4] : center(b);
      const r = await pickAndPin(page, () => altClick(page, cx, cy));
      if (!r) {
        check(label(`${t.key}: pick posted`), false);
        continue;
      }
      picks[t.key] = r;
      const fw = r.pick.draft.framework;
      const s = fw.source;
      const actual = s ? `${s.file.replace(/^.*\/(?=src\/)/, '').replace(/\?.*$/, '')}:${s.line}` : 'none';
      const ok =
        !!s && s.file.replace(/\?.*$/, '').endsWith(t.file) && s.line === t.line && s.precision === 'element' &&
        fw.name === fx.framework && fx.version.test(fw.version ?? '') &&
        fw.componentChain.find((c) => c.type === 'component')?.name === t.chain0 &&
        r.pick.draft.repeat?.count === t.repeat.count && r.pick.draft.repeat?.index === t.repeat.index &&
        r.pick.draft.element?.tag === t.tag && r.pick.draft.kind === 'element';
      check(label(`${t.key} → ${t.file}:${t.line}`), ok, `actual ${actual} ${s?.precision} v${fw.version} chain[0]=${fw.componentChain[0]?.name} repeat ${r.pick.draft.repeat?.index}/${r.pick.draft.repeat?.count}`);
      table.push({ fixture: fx.name, target: t.key, expected: `${t.file.split('/').pop()}:${t.line}`, actual: actual.split('/').pop(), precision: s?.precision, repeat: `${r.pick.draft.repeat?.index}/${r.pick.draft.repeat?.count}`, ok });
    }

    // ---- payload shape (first pick)
    const p0 = picks['card title (3rd)']?.pick;
    if (p0) {
      const d = p0.draft;
      const e = d.element;
      check(label('payload: page info'), d.page.url.startsWith('http://localhost') && d.page.route === '/' && d.page.viewport.w === 1280 && ['light', 'dark'].includes(d.page.colorScheme) && Array.isArray(d.page.consoleErrors));
      check(label('payload: element info'), !!e && e.selector.length > 0 && e.altSelectors.length <= 3 && e.text === 'Project 3' && e.outerHTML.includes('card-title') && e.rect.w > 0 && 'font-size' in e.computedStyles && e.attributes['data-testid'] === 'card-title');
      const sel = await page.evaluate((s) => document.querySelectorAll(s).length, e.selector);
      check(label('payload: selector is unique'), sel === 1, e.selector);
      check(label('payload: props (React) / null (Svelte)'), fx.framework === 'svelte' ? d.framework.props == null : d.framework.props?.title === 'Project 3', JSON.stringify(d.framework.props));
      check(label('anchor + rect + viewport'), p0.anchor.tag === 'h2' && p0.anchor.textFingerprint === 'project 3' && p0.anchor.repeatIndex === 2 && p0.anchor.source?.line === exp.title && p0.rect.w > 0 && p0.viewport.dpr >= 1);
    }

    // ---- page did not react
    const invited = await page.getByText('Invited').count();
    const starred = await page.getByText('Starred').count();
    check(label('alt+click: no form submit, no Star toggle, no navigation'), invited === 0 && starred === 0 && page.url() === urlBefore, `url ${page.url()}`);

    // ---- pins render, state changes, move on scroll
    const pin1 = picks['card title (3rd)'].id;
    await sleep(150);
    const n0 = await pinNode(page, pin1);
    check(label('pin drawn with its number'), !!n0 && n0.text === String(pin1) && n0.display !== 'none', JSON.stringify(n0));
    await page.evaluate(() => window.scrollTo(0, 150));
    await sleep(250);
    const maxScroll = await page.evaluate(() => window.scrollY);
    const n1 = await pinNode(page, pin1);
    check(label('pin moves on scroll'), !!n0 && !!n1 && Math.abs(n0.y - n1.y - maxScroll) <= 2 && maxScroll > 0, `dy ${n0 && n1 ? n0.y - n1.y : '?'} scrollY ${maxScroll}`);
    await reply(page, { t: 'pin-highlight', id: pin1 });
    await sleep(120);
    const hl = (await pinNode(page, pin1))?.state;
    await reply(page, { t: 'pin-highlight', id: null });
    await reply(page, { t: 'pin-dim', ids: [pin1], dimmed: true });
    await sleep(120);
    const dim = (await pinNode(page, pin1))?.state;
    await reply(page, { t: 'pin-dim', ids: [pin1], dimmed: false });
    check(label('pin states: highlighted / dimmed'), hl === 'highlighted' && dim === 'dimmed', `${hl} ${dim}`);

    // ---- overlay-set / ack
    await reply(page, { t: 'overlay-set', reqId: 'r1', visible: false });
    const ack = await take(page, 'overlay-ack', 3000);
    const hidden = await page.evaluate(() => document.getElementById('sidecall-picker-root').style.display === 'none');
    await reply(page, { t: 'overlay-set', reqId: 'r2', visible: true });
    const ack2 = await take(page, 'overlay-ack', 3000);
    check(label('overlay-set hides/shows and acks'), ack?.reqId === 'r1' && ack.visible === false && hidden && ack2?.reqId === 'r2' && ack2.visible === true);
    await page.evaluate(() => window.scrollTo(0, 0));

    // ---- ↑ traversal moves to the parent (and the page doesn't scroll)
    {
      const b = await box(page, '[data-testid=card-title]', 1);
      const [cx, cy] = center(b);
      await page.evaluate(() => window.scrollTo(0, 40));
      await page.keyboard.down('Alt');
      await page.mouse.move(cx, cy - 40 + 3);
      await page.mouse.move(cx, cy - 40, { steps: 3 });
      const label0 = await until(async () => page.evaluate(() => document.getElementById('sidecall-picker-root').shadowRoot.querySelector('[data-role=hover-label]').textContent), 3000);
      await page.keyboard.press('ArrowUp');
      await page.keyboard.press('ArrowUp');
      await page.keyboard.press('ArrowDown');
      await sleep(100);
      const scrollY = await page.evaluate(() => window.scrollY);
      await page.mouse.click(cx, cy - 40);
      await page.keyboard.up('Alt');
      const pk = await take(page, 'pick');
      check(label('↑↑↓ traversal: pick lands on the parent (header), page did not scroll'), pk?.draft.element?.tag === 'header' && scrollY === 40, `tag ${pk?.draft.element?.tag} scrollY ${scrollY}`);
      check(label('hover label shows component + file:line'), /Card\s+.*Card\.(svelte|tsx):\d+/.test(label0 ?? ''), label0 ?? '');
      await page.evaluate(() => window.scrollTo(0, 0));
      if (pk) {
        const id = nextPin++;
        await reply(page, { t: 'pin-add', localId: pk.localId, pin: { id, anchor: pk.anchor, label: 'x', dimmed: false } });
        await reply(page, { t: 'pin-remove', id });
        await sleep(150);
        check(label('pin-remove removes the pin'), (await pinNode(page, id)) === null);
      }
    }

    // ---- region pick by alt+drag over several cards
    {
      const a = await box(page, '.card', 0);
      const c = await box(page, '.card', 2);
      await page.evaluate(() => window.getSelection()?.removeAllRanges());
      await altDrag(page, a.x + 2, a.y + 2, c.x + c.width - 2, c.y + c.height - 2);
      const r = await pickAndPin(page, async () => {});
      const rect = r?.pick.draft.region?.rect;
      const els = r?.pick.draft.region?.elements ?? [];
      const sel = await page.evaluate(() => window.getSelection()?.toString() ?? '');
      check(label('alt+drag → region pick with elements'), r?.pick.draft.kind === 'region' && !!rect && rect.w > 50 && els.length >= 3 && els.length <= 20 && els.some((e) => e.tag === 'article') && r.pick.draft.element === null && r.pick.anchor.region !== null, `${els.length} elements, tags ${[...new Set(els.map((e) => e.tag))].join(',')}`);
      check(label('alt+drag: no text selection'), sel === '');
      if (r) {
        await sleep(150);
        const pn = await pinNode(page, r.id);
        check(label('region pin drawn'), !!pn && pn.display !== 'none');
      }
    }

    // ---- console errors captured
    {
      await page.evaluate(() => {
        console.error('e2e console error', { a: 1 });
        setTimeout(() => { throw new Error('e2e uncaught'); }, 0);
        Promise.reject(new Error('e2e rejection'));
      });
      await sleep(200);
      const b = await box(page, '[data-testid=card-title]', 0);
      const r = await pickAndPin(page, () => altClick(page, ...center(b)));
      const errs = r?.pick.draft.page.consoleErrors ?? [];
      check(label('console errors in page.consoleErrors'), errs.some((e) => e.includes('e2e console error')) && errs.some((e) => e.includes('e2e uncaught')) && errs.some((e) => e.includes('e2e rejection')), `${errs.length} entries`);
    }

    // ---- Esc posts 'escape'
    await page.keyboard.press('Escape');
    check(label("Esc posts 'escape'"), !!(await take(page, 'escape', 3000)));

    // ---- HMR: edit the temp copy's Card file; pins must stay ok and on the same card
    await page.evaluate(() => { window.__msgs.length = 0; });
    const titlePin = picks['card title (3rd)'];
    const rootPin = picks['card root (4th)'];
    const before = await pinsSnapshot(page);
    const hotBefore = hotUpdates;
    const orig = readFileSync(cardFile, 'utf8');
    writeFileSync(cardFile, fx.hmrPrefix + orig);
    const hot = await until(() => hotUpdates > hotBefore, 20000, 100);
    await sleep(900);
    const after = await pinsSnapshot(page);
    const statuses = await page.evaluate(() => window.__msgs.filter((m) => m.t === 'pin-status').map((m) => `${m.id}:${m.status}`));
    const find = (arr, id) => arr?.find((p) => p.id === id);
    check(label('HMR applied'), !!hot);
    check(
      label('HMR: pins stay ok and on the same card (text)'),
      [titlePin, rootPin].every((p) => find(after, p.id)?.status === 'ok' && find(after, p.id)?.text === find(before, p.id)?.text && find(after, p.id)?.text),
      `title "${find(after, titlePin.id)?.text}" root "${find(after, rootPin.id)?.text}" status msgs [${statuses}]`,
    );
    check(label('HMR: no stale flap reported'), !statuses.some((s) => s.endsWith(':stale')), statuses.join(','));
    const same = await page.evaluate((id) => {
      // The pin's element must now be inside the 3rd card (index 2), whose title is Project 3.
      const e = window.__sidecallPicker.pins.snapshot().find((p) => p.id === id);
      return e?.text;
    }, titlePin.id);
    check(label('HMR: pin still on "Project 3"'), same === 'Project 3', String(same));
    const pn = await pinNode(page, titlePin.id);
    const tb = await box(page, '[data-testid=card-title]', 2);
    check(label('HMR: pin drawn at the card title'), !!pn && Math.abs(pn.x - tb.x) <= 20 && Math.abs(pn.y - tb.y) <= 20 && pn.state === 'normal', JSON.stringify(pn));
    // A fresh pick after HMR sees the shifted line (+1)
    {
      const [cx, cy] = center(tb);
      await altClick(page, cx, cy);
      const pk = await take(page, 'pick');
      check(label('HMR: fresh pick reports the shifted line'), pk?.draft.framework.source?.line === exp.title + 1 && pk.draft.framework.source.precision === 'element', `line ${pk?.draft.framework.source?.line} expected ${exp.title + 1}`);
      if (pk) await reply(page, { t: 'pin-add', localId: pk.localId, pin: { id: nextPin++, anchor: pk.anchor, label: 'x', dimmed: false } });
    }

    // ---- reload: 'ready', then pins-restore re-anchors (with the pre-HMR anchors)
    const restore = [
      { id: titlePin.id, anchor: titlePin.pick.anchor, label: 'x', dimmed: false },
      { id: rootPin.id, anchor: rootPin.pick.anchor, label: 'x', dimmed: false },
      { id: 99, anchor: { tag: 'h2', source: { file: 'src/Gone.svelte', line: 1, column: 1 }, selector: '#nope', altSelectors: [], textFingerprint: 'does not exist', repeatIndex: null, region: null }, label: 'x', dimmed: false },
    ];
    await page.reload();
    await page.locator('.card').first().waitFor();
    const ready2 = await take(page, 'ready');
    check(label("after reload: 'ready' posted"), !!ready2);
    await reply(page, { t: 'pins-restore', pins: restore });
    const st = {};
    const t0 = Date.now();
    await until(async () => {
      const m = await page.evaluate(() => { const o = window.__msgs.filter((x) => x.t === 'pin-status'); return o; });
      for (const x of m) st[x.id] = x;
      return st[titlePin.id] && st[rootPin.id];
    }, 8000);
    const snap = await pinsSnapshot(page);
    check(label('reload: pins-restore re-anchors to the same cards'), st[titlePin.id]?.status === 'ok' && st[rootPin.id]?.status === 'ok' && find(snap, titlePin.id)?.text === 'Project 3' && /^Project 4/.test(find(snap, rootPin.id)?.text ?? ''), `title ${find(snap, titlePin.id)?.text} root ${find(snap, rootPin.id)?.text} in ${Date.now() - t0}ms`);
    // The pin is drawn on the frame after its status arrives, so wait for it rather than racing it.
    let pn2 = null;
    await until(async () => {
      pn2 = await pinNode(page, titlePin.id);
      return !!pn2 && pn2.display !== 'none' && pn2.state === 'normal';
    }, 3000);
    check(label('reload: restored pin drawn'), !!pn2 && pn2.display !== 'none' && pn2.state === 'normal');
    const gone = await until(async () => {
      const m = await page.evaluate(() => window.__msgs.filter((x) => x.t === 'pin-status' && x.id === 99));
      return m[0] ?? null;
    }, 8000);
    check(label('reload: an unmatched pin becomes stale after the grace period'), gone?.status === 'stale' && (await pinNode(page, 99))?.state === 'stale', JSON.stringify(gone));

    check(label('no picker errors/warnings in the page'), problems.length === 0, problems.slice(0, 3).join(' | '));
    writeFileSync(cardFile, orig);
  } catch (err) {
    check(label('run completed'), false, String(err?.stack ?? err));
  } finally {
    await page.close().catch(() => {});
    vite.kill('SIGTERM');
  }
}

// ------------------------------------------------------------------ main
const tmp = mkdtempSync(join(tmpdir(), 'sidecall-picker-e2e-'));
const profile = join(tmp, 'profile');
let ctx;
try {
  ctx = await chromium.launchPersistentContext(profile, {
    channel: 'chromium',
    headless: true,
    viewport: { width: 1280, height: 800 },
    args: ['--headless=new', `--disable-extensions-except=${EXT_DIR}`, `--load-extension=${EXT_DIR}`],
  });
  for (const [i, fx] of FIXTURES.entries()) {
    if (only && fx.name !== only) continue;
    console.log(`\n== ${fx.name}`);
    await runFixture(fx, i, ctx, tmp);
  }
} finally {
  await ctx?.close().catch(() => {});
  rmSync(tmp, { recursive: true, force: true });
}
console.log('\nfixture | target | expected | actual | precision | repeat');
for (const r of table) console.log(`${r.fixture} | ${r.target} | ${r.expected} | ${r.actual} | ${r.precision} | ${r.repeat} ${r.ok ? 'OK' : 'FAIL'}`);
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
