// Automated checks for spikes 1 and 2 in Playwright's bundled Chromium.
// Uses the e2e build (.output/chrome-mv3-e2e) so its SW doesn't touch spike-3 servers.
//
//   A: no grant, real permission UI (no --use-fake-ui-for-media-stream) -> offscreen gUM error name
//   B: fresh profile, fake UI auto-accept, NO options grant               -> does fake UI mask the offscreen limitation?
//   C: fresh profile, fake UI auto-accept, grant from options page      -> record works, echo test pipeline runs
//
// The side panel page is opened as a normal tab (automation can't open the real
// side panel without a user gesture); it is the same extension origin/context type
// for messaging purposes.
import { chromium } from 'playwright';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const EXT = path.resolve('.output/chrome-mv3-e2e');
const results = {};

async function launch(fakeUi, extraArgs = []) {
  const args = [
    `--disable-extensions-except=${EXT}`,
    `--load-extension=${EXT}`,
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required',
  ];
  if (fakeUi) args.push('--use-fake-ui-for-media-stream');
  args.push(...extraArgs);
  const ctx = await chromium.launchPersistentContext(mkdtempSync(path.join(tmpdir(), 'sc-spike-')), {
    channel: 'chromium', // new headless mode supports extensions
    headless: true,
    args,
  });
  let [sw] = ctx.serviceWorkers();
  sw ??= await ctx.waitForEvent('serviceworker');
  const id = new URL(sw.url()).host;
  return { ctx, id };
}

async function clickAndWait(page, button, outSel, timeout) {
  await page.evaluate((s) => delete document.querySelector(s).dataset.result, outSel);
  await page.click(button);
  await page.waitForFunction((s) => !!document.querySelector(s).dataset.result, outSel, { timeout });
  return JSON.parse((await page.textContent(outSel)).replace(/\n\(\d+ ms\)$/, ''));
}

// --- A + B ---
{
  const { ctx, id } = await launch(false);
  const page = await ctx.newPage();
  page.on('console', (m) => console.log('[panel]', m.text()));
  await page.goto(`chrome-extension://${id}/sidepanel.html`);
  results.A_permissionBefore = await page.textContent('#perm');
  results.A_noGrant = await clickAndWait(page, '#record5', '#record5-out', 20000);
  console.log('A', results.A_noGrant);

  // Note: ctx.grantPermissions() for chrome-extension:// fails with
  // "Permission can't be granted to opaque origins", so B uses fake UI instead.
  await ctx.close();
}

// --- B: fake-UI auto-accept, but NO options-page grant first ---
{
  const { ctx, id } = await launch(true);
  const page = await ctx.newPage();
  await page.goto(`chrome-extension://${id}/sidepanel.html`);
  results.B_fakeUiNoOptionsGrant = await clickAndWait(page, '#record5', '#record5-out', 30000);
  results.B_permissionAfter = await page.evaluate(async () => (await navigator.permissions.query({ name: 'microphone' })).state);
  console.log('B', { ...results.B_fakeUiNoOptionsGrant, settings: undefined }, results.B_permissionAfter);
  await ctx.close();
}

// --- C ---
{
  const { ctx, id } = await launch(true);
  const opt = await ctx.newPage();
  await opt.goto(`chrome-extension://${id}/options.html`);
  results.C_optionsBefore = await opt.textContent('#state');
  await opt.click('#grant');
  await opt.waitForFunction(() => document.querySelector('#out').textContent.length > 0);
  results.C_optionsOut = await opt.textContent('#out');
  results.C_optionsAfter = await opt.textContent('#state');
  await opt.close();

  const page = await ctx.newPage();
  page.on('console', (m) => console.log('[panel]', m.text()));
  await page.goto(`chrome-extension://${id}/sidepanel.html`);
  results.C_record = await clickAndWait(page, '#record5', '#record5-out', 30000);
  console.log('C', { ...results.C_record, settings: undefined });

  // Check offscreen doc survives the panel closing (spec §5.5 claim).
  await page.close();
  const page2 = await ctx.newPage();
  await page2.goto(`chrome-extension://${id}/sidepanel.html`);
  results.C_offscreenAfterPanelClose = await page2.evaluate(async () => {
    const c = await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] });
    return c.map((x) => x.documentUrl);
  });

  // Echo-test pipeline (fake device = synthetic beep, so leakage numbers are meaningless here).
  await page2.evaluate(() => delete document.querySelector('#echo-out').dataset.result);
  await page2.click('#echo');
  await page2.waitForFunction(() => !!document.querySelector('#echo-out').dataset.result, null, { timeout: 180000 });
  const echo = JSON.parse(await page2.textContent('#echo-out'));
  results.C_echo = echo.ok
    ? {
        ok: true,
        refDb: echo.refDb,
        captures: echo.captures.map(({ settings, ...c }) => ({ ...c, ec: settings.echoCancellation })),
        capabilities: echo.capabilities,
        userAgent: echo.userAgent,
      }
    : echo;
  results.C_echoSummaryText = await page2.textContent('#echo-summary');
  await ctx.close();
}

writeFileSync('logs/spike1-2-playwright.json', JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
