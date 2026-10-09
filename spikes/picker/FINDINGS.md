# Spike 4: picker source resolution (findings)

Milestone 0, item 4. Covers SPEC §5.2–5.4 and §12 risk 4.

**Result:** source resolution works for all three fixtures. Every alt+click target resolves to the exact file:line, including **React 19, which also reaches element precision** (the spec expected component precision). React DevTools coexistence works only with a small shim. Without it, bippy 0.7.3 stops React DevTools 8 from installing its hook whenever the picker runs first.

## Versions (2026-10-09)

| Thing | Version |
|---|---|
| svelte / @sveltejs/kit / vite-plugin-svelte | 5.57.2 / 3.0.1 / 7.3.1 |
| react, react-dom (react18-app) | 18.3.1 |
| react, react-dom (react19-app) | 19.3.0 |
| vite / @vitejs/plugin-react | 8.3.3 / 6.1.2 (oxc JSX transform) |
| bippy | 0.7.3 |
| source-map-js | 1.2.2 |
| esbuild / playwright | 0.28.2 / 1.63.0 (Chrome for Testing 153.0.8010.12) |
| React Developer Tools | 8.0.0 (CRX from the Web Store update URL) |
| node / pnpm | 22.17.1 / 10.28.1 |

## Results

The automated run is `node test.mjs`: headless Playwright Chromium with `launchPersistentContext`, `--load-extension` and `--headless=new`. The expected line is computed from the fixture source by matching the opening tag. Columns are reported as each source provides them (see gotchas). The coordinator re-ran the suite independently and got the same results.

**Picker only.** The three passes with React DevTools loaded produced identical source results.

| Fixture | Target | Expected | Actual | Precision | Repeat (idx/count) |
|---|---|---|---|---|---|
| svelte5-app | card title (3rd) | Card.svelte:8 | src/routes/components/Card.svelte:8:2 | element | 2/12 ✓ |
| svelte5-app | sidebar item (2nd) | Sidebar.svelte:10 | …/Sidebar.svelte:10:4 | element | 1/5 ✓ |
| svelte5-app | form submit | Form.svelte:15 | …/Form.svelte:15:1 | element | 0/1 ✓ |
| svelte5-app | card root `<article>` (4th) | Card.svelte:6 | …/Card.svelte:6:0 | element | 3/12 ✓ |
| svelte5-app | card button (1st) | Card.svelte:11 | …/Card.svelte:11:1 | element | 0/12 ✓ |
| react18-app | card title (3rd) | Card.tsx:10 | /abs/…/src/components/Card.tsx:10:9 | element | 2/12 ✓ |
| react18-app | sidebar item (2nd) | Sidebar.tsx:10 | …/Sidebar.tsx:10:13 | element | 1/5 ✓ |
| react18-app | form submit | Form.tsx:22 | …/Form.tsx:22:7 | element | 0/1 ✓ |
| react18-app | card root (4th) | Card.tsx:8 | …/Card.tsx:8:5 | element | 3/12 ✓ |
| react18-app | card button (1st) | Card.tsx:13 | …/Card.tsx:13:7 | element | 0/12 ✓ |
| react19-app | card title (3rd) | Card.tsx:10 | http://localhost:5275/src/components/Card.tsx:10:8 | element | 2/12 ✓ |
| react19-app | sidebar item (2nd) | Sidebar.tsx:10 | …/Sidebar.tsx:10:12 | element | 1/5 ✓ |
| react19-app | form submit | Form.tsx:22 | …/Form.tsx:22:6 | element | 0/1 ✓ |
| react19-app | card root (4th) | Card.tsx:8 | …/Card.tsx:8:4 | element | 3/12 ✓ |
| react19-app | card button (1st) | Card.tsx:13 | …/Card.tsx:13:6 | element | 0/12 ✓ |

**After an HMR edit.** The test prepends one line to Card and waits for the hot update, so every line shifts by +1:

| Fixture | Target | Expected | Actual | Precision |
|---|---|---|---|---|
| svelte5-app | card title / card root | 9 / 7 | 9 / 7 ✓ | element |
| react18-app | card title / card root | 11 / 9 | 11 / 9 ✓ (needs `getLatestFiber`) | element |
| react19-app | card title | 11 | 11 ✓ (needs the fresh-element trick) | element |
| react19-app | card root (a component's root element) | 9 | 8: stale, detected | component (downgraded) |

**Other checks**
- **No side effects:** alt+click never triggered page behaviour. The form didn't submit, "Star" didn't toggle, and neither the nav nor the URL changed.
- **Resolve time:** Svelte and React 18 take under 1 ms. React 19 takes 15–25 ms on the first pick in a module (it fetches the module and decodes its inline source map), then 1–2 ms once cached.
- **Component chains:**
  - Svelte: `Card (CardList.svelte:8) → {#each} (CardList.svelte:7) → CardList (+page.svelte:12)`
  - React, both versions: `Card (CardList.tsx:8) → CardList (App.tsx:12) → App (main.tsx:8)`

## Does it run before React? (document_start)

Yes, in every run. At boot the picker sees `document.readyState === "loading"` and `document.scripts.length === 0`. bippy's hook catches React's `inject()` about 40–70 ms later and records `{version, bundleType: 1}`. Vite loads React as a deferred ES module, so a manifest `"world": "MAIN", "run_at": "document_start"` content script always runs first.

Even if the picker ran late, source resolution would still work. `getFiberFromHostInstance` reads the DOM node's `__reactFiber$…` key, and `_debugSource`/`_debugStack` live on the fibers. **The hook only gives us the renderer version.**

## HMR

- **Svelte:** works. HMR re-creates the DOM, and the new nodes carry new `__svelte_meta`.
- **React 18:** the DOM node's `__reactFiber$` key points at the fiber the node was created with. After an update, the committed fiber may be its `alternate`, which holds a stale `_debugSource`. Fix: `getLatestFiber(getFiberFromHostInstance(el))`.
- **React 19:** `_debugStack` is set only in `createFiberFromElement`. `createWorkInProgress` copies it from `current`, and reconciliation never refreshes it. After a state-preserving Fast Refresh the fiber still holds the first render's stack (old module version, old line). Vite serves only the current module, so the old frame maps to the wrong line. The fix has two parts:
  1. **Fresh element.** If the parent fiber is a host or Fragment fiber, take `parent.memoizedProps.children[fiber.index]`. Check that its type and key match, then use that element's `_debugStack`, which comes from the latest render.
  2. **Staleness detection.** A component's root element has a composite parent, so the first fix doesn't apply. Instead, compare the frame URL's `?t=` with the newest `?t=` Vite imported for that path (`performance.getEntriesByType('resource')`). If the frame's is older, keep the file and downgrade to `precision: "component"`.

## React DevTools coexistence

**Without a fix it breaks, and the outcome depends on content-script order.**
- **What bippy does:** bippy 0.7.3 installs `__REACT_DEVTOOLS_GLOBAL_HOOK__` as a configurable accessor. It patches `window.hasOwnProperty` so that only the **first** call for that key returns false.
- **What DevTools does:** React DevTools 8 (`build/installHook.js`) checks twice: an outer `if (!window.hasOwnProperty(...))`, then `if (e.hasOwnProperty(...)) return null` inside `installHook()`. The outer check uses up bippy's one-shot spoof, so the inner check bails.
- **Picker first:** DevTools' hook is never installed, and the Components panel has nothing to attach to. Reproduced deterministically.
- **DevTools first:** bippy patches DevTools' `inject()` and everything works.

The order isn't controlled by the `--load-extension` order and appears to depend on the extension ID. DevTools registers its hook dynamically, via `chrome.scripting.registerContentScripts`.

**The fix** is in `src/picker.ts`, in the "React DevTools coexistence" block. It replaces `window.hasOwnProperty` with a shim that returns false for the hook key while the current hook has `_isBippyHook === true`, and otherwise uses the native method. DevTools then installs its own non-configurable hook.

**Result with the shim**, in both load orders and on every fixture:
- The final hook is DevTools' (`_isBippyHook` false).
- `renderers.size === 1` and `rendererInterfaces.size === 1`, so the DevTools backend is attached.
- Picker results are identical to the picker-only pass.

**Caveats:**
- bippy's inject listener doesn't fire when DevTools' hook wins, so the picker reads the version from `hook.renderers`.
- Worth reporting upstream to bippy.
- The DevTools panel UI can't be verified headlessly; see the manual steps.

## Exact APIs used

**Svelte (dev only)**
- `el.__svelte_meta = { loc: { file, line, column }, parent: DevStackEntry | null }`, from `svelte/src/internal/client/dev/elements.js`.
- `DevStackEntry = { type: 'component'|'if'|'each'|'await'|'key'|'render', file, line, column, parent, componentTag? }`, from `context.js` `add_svelte_meta`.
- An entry's file and line are **where the block or component tag is written**, which is the parent file.
- `loc.file` is project-relative. `line` is 1-based and `column` is 0-based.
- **Version:** `window.__svelte.v` is a `Set` holding only the major version ("5").
- Elements without meta are handled by walking up `parentElement`.

**bippy**
- From `bippy`: `getFiberFromHostInstance`, `getLatestFiber`, `isCompositeFiber`, `isHostFiber`, `getDisplayName`, `getRDTHook`, `_renderers`, `onRendererInject`.
- From `bippy/source`: `getSource(fiber)` and `parseStack()`. Also available: `getOwnerStack`, `symbolicateStack`, `getSourceMap`.
- `getSource` tries, in order: `_debugSource`; the fiber's own `_debugStack`, symbolicated by fetching the `sourceMappingURL` (inline data URLs included); an owned child's stack; then the throwing-dispatcher fallback.

**React 18**
- `fiber._debugSource = { fileName (absolute), lineNumber, columnNumber (1-based) }`.
- Vite 8 with plugin-react 6 (oxc) emits it by default.

**React 19**
- There is no `_debugSource`; `jsxDEV` ignores its source argument.
- `fiber._debugStack` and `element._debugStack` are `Error("react-stack-top-frame")`, with `stackTraceLimit` 10.
- The first non-`node_modules` frame is that element's `_jsxDEV(...)` call. Its source-map position is the element's own line.

**Manual fallback (implemented)**
- Fetch the module and decode its inline base64 source map.
- Call source-map-js `originalPositionFor({ line, column: col - 1 })`.
- Resolve `sources[i]` against the module URL.
- It agreed with bippy on every pick.

## Gotchas

1. **bippy's root import pulls in `react`.** Alias it to an empty stub in esbuild (`src/react-stub.js`). Don't use `bippy/dist/core.js`; its exports are minified. The bundle is about 75 KB minified.
2. **bippy `getSource` returns the source map's `sources` entry verbatim.** Under Vite that's only a basename (`Card.tsx`), so resolve it against the module URL.
3. **Three path forms reach the daemon:**
   - project-relative (Svelte)
   - absolute filesystem path (React 18)
   - dev-server URL with `?t=` (React 19)
4. **Column bases differ:** Svelte is 0-based, React 18 1-based, React 19 0-based.
5. **Use `getLatestFiber`.** Without it, the fiber from the DOM node can be the stale alternate.
6. **React 19 keeps a stale `_debugStack` after HMR.** See the HMR section.
7. **React 19 owner-stack cap.** Elements beyond 10,000 created within 1 s share a placeholder stack with no user frame. The picker then falls back to the owner's type.
8. **The SvelteKit chain runs into `node_modules`.** It continues into kit's `root.svelte`/`layout.svelte`; filter those entries out.
9. **SvelteKit 3 setup:** there's no `svelte.config.js` (options go in `sveltekit({...})`), and it needs vite ≥ 8.0.12 and TypeScript 6 as peers.
10. **The repeat scan reads `.stack` on every React 19 fiber.** Fine on the fixtures; cache it in the real picker.
11. **Ports 5173 and 5174 were busy on this machine.** The fixtures honour a `PORT` env var, and the test uses 5273–5275 (override with `PORT_BASE`).
12. **Test environment:** Chrome for Testing still accepts `--load-extension`; branded Chrome doesn't. Runs are headless and extensions work there, but a macOS Dock icon may flash. The single 404 on each page is the favicon.

## Recommended SPEC changes

- **§5.2, bullet 2:** "It must run in the MAIN world. It should load before React so bippy's hook sees renderer injection (used for the version); source resolution reads fibers from DOM nodes and doesn't depend on load order. The picker must keep `window.hasOwnProperty('__REACT_DEVTOOLS_GLOBAL_HOOK__')` returning false while bippy's hook is current, so React DevTools can replace it."
- **§5.4 React 19:** "Resolve the host fiber's `_debugStack` (the element's `jsxDEV` call site) through the dev server's source map (bippy `getSource`) → precision `element`. Prefer the latest element's `_debugStack` from the parent host fiber's `memoizedProps.children`. If the frame's `?t=` is older than the latest HMR import, keep the file and use precision `component`."
- **§5.4 React, add:** "Always apply `getLatestFiber()`. DevTools coexistence is verified and requires the shim."
- **§5.4 Svelte, add:** "Chain entries point at the tag in the parent file. Drop `node_modules/` entries. Only the major version is exposed at runtime."
- **§5.4 Path normalisation, add:** "Accept project-relative, absolute and dev-server-URL inputs. Normalise columns to 1-based."
- **§5.4 Repeat detection (React):** "Key on the same `_debugSource` (≤18), or the same first user frame of the element's `_debugStack` with the query stripped (19); fall back to component type plus owner."
- **§13:** React 19 precision resolved; element precision works without a dev plugin.
- **§12.4:** de-risked.

## How to run

Fixtures (each is standalone):

```
cd fixtures/svelte5-app && pnpm install && pnpm dev   # :5173 (PORT=… to override)
cd fixtures/react18-app && pnpm install && pnpm dev   # :5174
cd fixtures/react19-app && pnpm install && pnpm dev   # :5175
```

Spike:

```
cd spikes/picker && pnpm install
pnpm build      # → ext/picker.js, plus ext-noshim/ for the DevTools conflict demo
node test.mjs   # starts each fixture on 5273-5275, runs all passes, prints the table
node test.mjs --no-devtools --no-hmr --only=react19-app   # subsets; --headed for debugging
```

React DevTools for the coexistence passes is unpacked into `.cache/react-devtools`, which is gitignored:

```
mkdir -p .cache/download && cd .cache/download
curl -sSL -o rdt.crx 'https://clients2.google.com/service/update2/crx?response=redirect&prodversion=153.0&acceptformat=crx2,crx3&x=id%3Dfmkadmapgofadopljbjfkapdkoienihi%26uc'
N=$(python3 -c "import struct;print(struct.unpack('<I',open('rdt.crx','rb').read()[8:12])[0])")
tail -c +$((12 + N + 1)) rdt.crx > rdt.zip && mkdir -p ../react-devtools && unzip -q rdt.zip -d ../react-devtools
```

Picks are exposed as `window.__sidecallLastPick` (all picks in `window.__sidecallPicks`), logged as `[sidecall] pick` and dispatched as a `sidecall:pick` event. Boot diagnostics are in `window.__sidecallBoot`.

## Manual test steps (your own Chrome)

Setup:
1. Run `cd spikes/picker && pnpm install && pnpm build`.
2. In `chrome://extensions`, enable Developer mode, click "Load unpacked" and choose `spikes/picker/ext`.
3. Run `cd fixtures/react19-app && pnpm dev` and open http://localhost:5175.

Steps:
1. **The picker loads first.** In the console run `__sidecallBoot`. Expected: `readyState: "loading"` and `scriptsInDocAtBoot: 0`.
2. **Card pick.** Alt+click "Project 3". Expected: the console logs `[sidecall] pick` with `…/src/components/Card.tsx`, line 10, precision `element` and repeat `{index: 2, count: 12}`. The page does nothing.
3. **Sidebar and form picks.** Alt+click "Projects" in the sidebar, then "Send invite". Expected: Sidebar.tsx:10 and Form.tsx:22. The nav doesn't change and the form doesn't submit.
4. **Plain clicks still work.** Click "Star" without Alt. Expected: it toggles to "Starred".
5. **DevTools hook.** With React Developer Tools enabled, reload and run `__REACT_DEVTOOLS_GLOBAL_HOOK__._isBippyHook, __REACT_DEVTOOLS_GLOBAL_HOOK__.rendererInterfaces.size`. Expected: `undefined 1`.
6. **DevTools panel.** Open the ⚛ Components tab. Expected:
   - The tree shows `App → Sidebar, CardList → Card×12, Form`.
   - Selecting a Card shows its props.
   - The toolbar icon shows the development build.
7. **Picker with DevTools open.** Repeat step 2 with the Components tab open. Expected: the same result.
8. **Without the shim.** Load `ext-noshim` instead of `ext` and reload. Expected: if `_isBippyHook` is `true`, the Components tab is empty or says the page doesn't appear to be using React. This confirms the shim is needed; whether it breaks depends on load order.
9. **HMR.** Prepend a blank line to `fixtures/react19-app/src/components/Card.tsx` and save.
   - Alt+click a card title. Expected: line 11, precision `element`.
   - Alt+click a card's border area. Expected: Card.tsx, precision `component` and `debug.stale: true`.
   - Revert the edit.
10. **Svelte.** Open :5173 and alt+click "Project 3". Expected: `src/routes/components/Card.svelte:8`, precision `element`, and chain `Card → {#each} → CardList`.
11. **React 18.** Open :5174 and alt+click "Project 3". Expected: an absolute path ending in `src/components/Card.tsx`, line 10, precision `element`.
