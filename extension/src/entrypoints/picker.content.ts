// The picker, in the page's own (MAIN) world so it can read framework internals
// (__svelte_meta, React fibers). It must start before page scripts: console capture and
// bippy's React hook have to be in place first. Talks to picker-bridge.content.ts via
// window.postMessage; see lib/picker-protocol.ts.
import { bootPicker } from '../lib/picker/boot';

export default defineContentScript({
  matches: ['http://localhost/*', 'http://127.0.0.1/*'],
  world: 'MAIN',
  runAt: 'document_start',
  allFrames: false,
  noScriptStartedPostMessage: true,
  main() {
    bootPicker();
  },
});
