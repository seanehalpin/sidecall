// Isolated-world half of the picker: relays between the page (window.postMessage + nonce)
// and the service worker (chrome.runtime). Top frame only.
import { Bridge } from '../lib/picker/bridge';
import { PICKER_RUNTIME_KIND } from '../lib/picker-protocol';

export default defineContentScript({
  matches: ['http://localhost/*', 'http://127.0.0.1/*'],
  runAt: 'document_start',
  allFrames: false,
  main() {
    const bridge = new Bridge({
      win: window,
      sendUp: (m) => browser.runtime.sendMessage(m),
    });
    window.addEventListener('message', bridge.onWindowMessage);
    browser.runtime.onMessage.addListener((m) => {
      if ((m as { kind?: string } | null)?.kind === PICKER_RUNTIME_KIND) bridge.onRuntimeMessage(m);
      return false;
    });
  },
});
