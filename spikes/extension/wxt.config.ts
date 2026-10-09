import { defineConfig } from 'wxt';

// See FINDINGS.md. WXT 0.21.x: entrypoints are discovered by file name under
// entrypoints/ (sidepanel/, options/, background.ts); any other *.html (here
// offscreen/index.html) becomes an "unlisted page" that is bundled but not
// referenced from the manifest — exactly what chrome.offscreen needs.
export default defineConfig({
  manifest: {
    name: 'Sidecall spike (M0 1-3)',
    // sidePanel is added automatically by WXT when a sidepanel entrypoint exists.
    permissions: ['offscreen', 'storage', 'alarms'],
    // Allow WS to the local daemon. MV3 default CSP already permits
    // ws://127.0.0.1 from the SW (connect-src is not restricted by default).
    action: { default_title: 'Open Sidecall spike panel' },
  },
});
