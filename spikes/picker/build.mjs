import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));

import fs from 'node:fs';

// ext/: the real spike. ext-noshim/: same picker without the DevTools hasOwnProperty shim, used to
// demonstrate the bippy/React DevTools 8 conflict.
fs.mkdirSync(`${here}ext-noshim`, { recursive: true });
fs.copyFileSync(`${here}ext/manifest.json`, `${here}ext-noshim/manifest.json`);
for (const [dir, shim] of [['ext', true], ['ext-noshim', false]]) await build({
  entryPoints: [`${here}src/picker.ts`],
  outfile: `${here}${dir}/picker.js`,
  define: { __RDT_SHIM__: String(shim) },
  bundle: true,
  format: 'iife',
  target: 'chrome120',
  sourcemap: 'inline',
  alias: { react: `${here}src/react-stub.js` },
  logLevel: 'info',
});
