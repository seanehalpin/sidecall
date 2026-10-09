// Sidecall picker spike: MAIN-world content script, run_at document_start.
// Importing bippy installs its DevTools-style hook as a side effect, so this must be the
// first thing that runs on the page (before React's module code).
import {
  getFiberFromHostInstance,
  getLatestFiber,
  getDisplayName,
  getRDTHook,
  isCompositeFiber,
  isHostFiber,
  onRendererInject,
  _renderers,
  type Fiber,
} from 'bippy';
import { getSource, parseStack } from 'bippy/source';
import { SourceMapConsumer } from 'source-map-js';

type Precision = 'element' | 'component';
type Source = { file: string; line: number; column: number; precision: Precision };
type ChainEntry = { name: string; type?: string; file?: string; line?: number; column?: number };
type PickResult = {
  framework: { name: 'svelte' | 'react' | 'unknown'; version?: string; source?: Source; componentChain: ChainEntry[] };
  repeat?: { count: number; index: number };
  debug: Record<string, unknown>;
  timings: { resolveMs: number };
  target: { tag: string; text: string };
};

declare global {
  interface Window {
    __sidecallLastPick?: PickResult;
    __sidecallPicks?: PickResult[];
    __sidecallBoot?: Record<string, unknown>;
    __svelte?: { v?: Set<string> };
  }
}

// ---------------------------------------------------------------- React DevTools coexistence
// bippy (0.7.3) installs its hook as an accessor on window and spoofs window.hasOwnProperty so the
// *first* `hasOwnProperty('__REACT_DEVTOOLS_GLOBAL_HOOK__')` returns false, letting the real React
// DevTools install over it. React DevTools 8's installHook.js checks twice (outer guard, then again
// inside installHook()), so the one-shot spoof is consumed by the first check and DevTools bails out
// (`return null`) -> DevTools never installs its hook and the Components panel is dead.
// Fix: keep answering `false` while the current hook is still bippy's. DevTools then replaces the
// accessor with its own (non-configurable) property; from then on we defer to the native method.
const HOOK_KEY = '__REACT_DEVTOOLS_GLOBAL_HOOK__';
const nativeHasOwn = Object.prototype.hasOwnProperty;
const hopShim = function (this: unknown, key: PropertyKey): boolean {
  if (key === HOOK_KEY && this === window) {
    const h = (window as unknown as Record<string, { _isBippyHook?: boolean } | undefined>)[HOOK_KEY];
    if (h?._isBippyHook === true) return false;
  }
  return nativeHasOwn.call(this, key);
};
declare const __RDT_SHIM__: boolean;
if (__RDT_SHIM__) {
  Object.defineProperty(window, 'hasOwnProperty', { configurable: true, writable: true, value: hopShim });
}

// ---------------------------------------------------------------- boot diagnostics
const boot: Record<string, unknown> = {
  at: performance.now(),
  readyState: document.readyState,
  scriptsInDocAtBoot: document.scripts.length,
  hookExistedBefore: false, // filled below (bippy may already have run by the time this line executes)
  rendererInjections: [] as { at: number; version?: string; bundleType?: number }[],
};
window.__sidecallBoot = boot;
try {
  const hook = getRDTHook() as unknown as Record<string, unknown> | undefined;
  boot.hookIsBippy = !!hook && hook._isBippyHook === true;
} catch {
  /* ignore */
}
onRendererInject((renderer: { version?: string; bundleType?: number }) => {
  (boot.rendererInjections as unknown[]).push({
    at: performance.now(),
    version: renderer.version,
    bundleType: renderer.bundleType,
  });
});
console.debug('[sidecall] picker loaded at', boot.at, 'readyState', document.readyState);

// ---------------------------------------------------------------- Svelte
type SvelteLoc = { file: string; line: number; column: number };
type SvelteStackEntry = {
  type: string;
  file?: string;
  line?: number;
  column?: number;
  componentTag?: string;
  parent: SvelteStackEntry | null;
};
type SvelteMeta = { loc?: SvelteLoc; parent?: SvelteStackEntry | null };
const metaOf = (el: Element): SvelteMeta | null | undefined =>
  (el as unknown as { __svelte_meta?: SvelteMeta | null }).__svelte_meta;

function resolveSvelte(target: Element): PickResult['framework'] & { repeat?: PickResult['repeat']; debug: Record<string, unknown> } | null {
  let el: Element | null = target;
  let hops = 0;
  while (el && !metaOf(el)?.loc) {
    el = el.parentElement;
    hops++;
  }
  const versions = window.__svelte?.v ? [...window.__svelte.v] : [];
  if (!el) {
    return versions.length ? { name: 'svelte', version: versions.join(','), componentChain: [], debug: { noMeta: true } } : null;
  }
  const meta = metaOf(el)!;
  const loc = meta.loc!;
  const chain: ChainEntry[] = [];
  for (let e = meta.parent ?? null; e; e = e.parent) {
    // Skip framework-internal entries (SvelteKit's root/layout components live in node_modules).
    if (e.file?.includes('node_modules/')) continue;
    chain.push({
      name: e.type === 'component' ? (e.componentTag ?? 'component') : `{#${e.type}}`,
      type: e.type,
      file: e.file,
      line: e.line,
      column: e.column,
    });
  }
  // repeat detection: same loc (file:line:column)
  const key = `${loc.file}:${loc.line}:${loc.column}`;
  let count = 0;
  let index = -1;
  for (const other of document.querySelectorAll('*')) {
    const l = metaOf(other)?.loc;
    if (l && `${l.file}:${l.line}:${l.column}` === key) {
      if (other === el) index = count;
      count++;
    }
  }
  return {
    name: 'svelte',
    version: versions.join(',') || undefined,
    source: { file: loc.file, line: loc.line, column: loc.column, precision: 'element' },
    componentChain: chain,
    repeat: { count, index },
    debug: { domHopsToMeta: hops },
  };
}

// ---------------------------------------------------------------- React
type DebugSource = { fileName: string; lineNumber: number; columnNumber?: number };
type FiberX = Fiber & { _debugSource?: DebugSource; _debugStack?: Error; _debugOwner?: FiberX | null };

function reactVersion(): string | undefined {
  const versions = new Set<string>();
  for (const r of _renderers as Set<{ version?: string }>) if (r.version) versions.add(r.version);
  try {
    const hook = getRDTHook() as unknown as { renderers?: Map<number, { version?: string }> };
    hook.renderers?.forEach((r) => r.version && versions.add(r.version));
  } catch {
    /* ignore */
  }
  return versions.size ? [...versions].join(',') : undefined;
}

const stripQuery = (u: string) => u.replace(/\?.*$/, '');
const isReactInternalFrame = (file: string) => /\/node_modules\//.test(file) || /\/\.vite\/deps\//.test(file);

/** First user-land frame of a React 19 `_debugStack` (the JSX call site), unsymbolicated. */
function firstUserFrame(stack: Error | undefined) {
  if (!stack?.stack) return null;
  const frames = parseStack(stack.stack);
  for (const f of frames) {
    if (!f.fileName || f.lineNumber == null) continue;
    if (isReactInternalFrame(f.fileName)) continue;
    return f;
  }
  return null;
}

// --- manual symbolication with source-map-js against the Vite-served module's inline map
const mapCache = new Map<string, Promise<SourceMapConsumer | null>>();
function loadMap(moduleUrl: string): Promise<SourceMapConsumer | null> {
  let p = mapCache.get(moduleUrl);
  if (!p) {
    p = (async () => {
      const res = await fetch(moduleUrl);
      if (!res.ok) return null;
      const code = await res.text();
      const m = /\/\/# sourceMappingURL=(\S+)\s*$/.exec(code);
      if (!m) return null;
      let raw: string;
      if (m[1].startsWith('data:')) {
        const b64 = m[1].slice(m[1].indexOf(',') + 1);
        raw = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
      } else {
        raw = await (await fetch(new URL(m[1], moduleUrl))).text();
      }
      return new SourceMapConsumer(JSON.parse(raw));
    })().catch(() => null);
    mapCache.set(moduleUrl, p);
  }
  return p;
}
async function symbolicateManual(frame: { fileName?: string; lineNumber?: number; columnNumber?: number }) {
  if (!frame.fileName || frame.lineNumber == null) return null;
  const consumer = await loadMap(frame.fileName);
  if (!consumer) return null;
  const pos = consumer.originalPositionFor({ line: frame.lineNumber, column: (frame.columnNumber ?? 1) - 1 });
  if (!pos.source || pos.line == null) return null;
  // Vite's map lists `sources` relative to the module URL (often just the basename, e.g. "Card.tsx"),
  // so resolve it against the module URL to recover the dev-server path.
  let file = pos.source;
  try {
    file = new URL(pos.source, stripQuery(frame.fileName)).href;
  } catch {
    /* keep as-is */
  }
  return { file, line: pos.line, column: pos.column ?? 0 };
}

function nearestFiber(target: Element): { fiber: FiberX; hops: number } | null {
  let el: Element | null = target;
  let hops = 0;
  while (el) {
    const f = getFiberFromHostInstance(el) as FiberX | null;
    // The DOM node's __reactFiber$ key points at the fiber it was created with; after updates the
    // committed fiber may be its alternate. getLatestFiber picks the current one.
    if (f) return { fiber: getLatestFiber(f) as FiberX, hops };
    el = el.parentElement;
    hops++;
  }
  return null;
}

type ElementLike = { $$typeof?: symbol; type?: unknown; key?: string | null; _debugStack?: Error };
/**
 * React 19 copies `_debugStack` from `current` when it reuses a fiber (createWorkInProgress) and
 * only sets it in createFiberFromElement, so after a state-preserving update (Fast Refresh/HMR) a
 * fiber's `_debugStack` still describes the *original* render (old module version + old line).
 * The freshest element for this fiber lives in its parent's memoizedProps.children when the parent
 * is a host or Fragment fiber; that element carries a `_debugStack` from the latest render.
 */
function freshDebugStack(fiber: FiberX): { stack: Error | undefined; fresh: boolean } {
  const parent = fiber.return as FiberX | null;
  if (parent && (isHostFiber(parent) || parent.tag === 7 /* Fragment */)) {
    const props = parent.memoizedProps as { children?: unknown } | unknown[] | null;
    const children = Array.isArray(props) ? props : (props as { children?: unknown } | null)?.children;
    const candidate = (Array.isArray(children) ? children[fiber.index] : children) as ElementLike | undefined;
    if (
      candidate &&
      typeof candidate === 'object' &&
      candidate.type === fiber.elementType &&
      (candidate.key ?? null) === (fiber.key ?? null) &&
      candidate._debugStack
    ) {
      return { stack: candidate._debugStack, fresh: true };
    }
  }
  return { stack: fiber._debugStack, fresh: false };
}

/** Latest `?t=` Vite has imported for a module path (HMR re-imports modules with a new t). */
function latestHmrT(path: string): number {
  let t = 0;
  for (const e of performance.getEntriesByType('resource')) {
    const u = e.name;
    if (stripQuery(u) !== path) continue;
    const m = /[?&]t=(\d+)/.exec(u);
    if (m) t = Math.max(t, Number(m[1]));
  }
  return t;
}
function frameT(url: string): number {
  const m = /[?&]t=(\d+)/.exec(url);
  return m ? Number(m[1]) : 0;
}

/** Cheap, synchronous per-element identity used for repeat detection. */
function reactRepeatKey(fiber: FiberX): string | null {
  if (fiber._debugSource) {
    const s = fiber._debugSource;
    return `src:${s.fileName}:${s.lineNumber}:${s.columnNumber ?? 0}`;
  }
  const f = firstUserFrame(freshDebugStack(fiber).stack);
  if (f) return `stk:${stripQuery(f.fileName!)}:${f.lineNumber}:${f.columnNumber}`;
  // fallback: component type + owner type
  const owner = fiber._debugOwner;
  return `own:${String(fiber.type)}:${owner ? getDisplayName(owner.type) : ''}`;
}

async function resolveReact(target: Element) {
  const found = nearestFiber(target);
  if (!found) return null;
  const { fiber, hops } = found;
  const version = reactVersion();
  const debug: Record<string, unknown> = {
    domHopsToFiber: hops,
    hostFiber: isHostFiber(fiber),
    hasDebugSource: !!fiber._debugSource,
    hasDebugStack: !!fiber._debugStack,
  };

  let source: Source | undefined;
  // React <=18: _debugSource on the nearest fiber that has one
  let f: FiberX | null = fiber;
  let fiberHops = 0;
  while (f && !f._debugSource) {
    f = f.return as FiberX | null;
    fiberHops++;
  }
  if (f?._debugSource) {
    const s = f._debugSource;
    source = {
      file: s.fileName,
      line: s.lineNumber,
      column: s.columnNumber ?? 0,
      precision: fiberHops === 0 ? 'element' : 'component',
    };
    debug.via = '_debugSource';
    debug.fiberHops = fiberHops;
  } else if (fiber._debugStack) {
    // React 19: owner stack. (a) bippy/source getSource; (b) manual source-map-js.
    const { stack, fresh } = freshDebugStack(fiber);
    debug.freshElementStack = fresh;
    const raw = firstUserFrame(stack);
    // Staleness: the frame's module version is older than the latest HMR import of that module.
    const stale = !!raw?.fileName && frameT(raw.fileName) < latestHmrT(stripQuery(raw.fileName));
    debug.stale = stale;
    debug.rawFrame = raw && `${raw.fileName}:${raw.lineNumber}:${raw.columnNumber}`;
    const t0 = performance.now();
    let bippySrc: Awaited<ReturnType<typeof getSource>> = null;
    try {
      // Hand bippy a view of the fiber whose _debugStack is the freshest one we found.
      const view = fresh ? (Object.create(fiber, { _debugStack: { value: stack } }) as FiberX) : fiber;
      bippySrc = await getSource(view);
    } catch (err) {
      debug.bippyError = String(err);
    }
    debug.bippyMs = Math.round(performance.now() - t0);
    debug.bippySource = bippySrc;
    const t1 = performance.now();
    const manual = raw ? await symbolicateManual(raw) : null;
    debug.manualMs = Math.round(performance.now() - t1);
    debug.manualSource = manual;
    let best: { file: string; line: number; column: number } | null = null;
    if (bippySrc && bippySrc.lineNumber != null) {
      // bippy returns the map's raw `sources` entry (basename under Vite); upgrade it to the full
      // dev-server path when the manual resolution agrees, else fall back to the raw frame URL.
      let file = bippySrc.fileName;
      const base = file.split('/').pop()!;
      if (manual && manual.file.endsWith('/' + base)) file = manual.file;
      else if (raw?.fileName && stripQuery(raw.fileName).endsWith('/' + base)) file = stripQuery(raw.fileName);
      best = { file, line: bippySrc.lineNumber, column: bippySrc.columnNumber ?? 0 };
    } else {
      best = manual;
    }
    if (best) {
      // The host fiber's own _debugStack points at the JSX call site of this very element.
      // A stale stack still names the right file but the line may have moved → component precision.
      source = { ...best, precision: stale ? 'component' : 'element' };
      debug.via = bippySrc?.lineNumber != null ? 'bippy/source getSource(_debugStack)' : 'source-map-js(_debugStack)';
    }
  }

  // component chain: composite fibers up the return path, with their usage site
  const chain: ChainEntry[] = [];
  for (let c = fiber.return as FiberX | null; c; c = c.return as FiberX | null) {
    if (!isCompositeFiber(c)) continue;
    const name = getDisplayName(c.type) ?? 'Anonymous';
    const entry: ChainEntry = { name, type: 'component' };
    if (c._debugSource) {
      entry.file = c._debugSource.fileName;
      entry.line = c._debugSource.lineNumber;
      entry.column = c._debugSource.columnNumber;
    } else if (c._debugStack) {
      const raw = firstUserFrame(freshDebugStack(c).stack);
      const s = raw ? await symbolicateManual(raw) : null;
      if (s) Object.assign(entry, s);
    }
    chain.push(entry);
    if (chain.length >= 15) break;
  }

  // repeat detection
  const key = reactRepeatKey(fiber);
  let count = 0;
  let index = -1;
  if (key) {
    for (const el of document.querySelectorAll('*')) {
      const f0 = getFiberFromHostInstance(el) as FiberX | null;
      const of = f0 ? (getLatestFiber(f0) as FiberX) : null;
      if (!of || of.stateNode !== el) continue;
      if (reactRepeatKey(of) === key) {
        if (of === fiber || of === fiber.alternate) index = count;
        count++;
      }
    }
  }
  debug.repeatKey = key;

  return { name: 'react' as const, version, source, componentChain: chain, repeat: { count, index }, debug };
}

// ---------------------------------------------------------------- pick
async function resolve(target: Element): Promise<PickResult> {
  const t0 = performance.now();
  const svelte = resolveSvelte(target);
  let framework: PickResult['framework'];
  let repeat: PickResult['repeat'];
  let debug: Record<string, unknown> = {};
  if (svelte) {
    ({ repeat, debug } = svelte);
    framework = { name: svelte.name, version: svelte.version, source: svelte.source, componentChain: svelte.componentChain };
  } else {
    const react = await resolveReact(target);
    if (react) {
      ({ repeat, debug } = react);
      framework = { name: 'react', version: react.version, source: react.source, componentChain: react.componentChain };
    } else {
      framework = { name: 'unknown', componentChain: [] };
    }
  }
  return {
    framework,
    repeat,
    debug,
    timings: { resolveMs: Math.round(performance.now() - t0) },
    target: { tag: target.tagName.toLowerCase(), text: (target.textContent ?? '').trim().slice(0, 80) },
  };
}

window.addEventListener(
  'click',
  (e) => {
    if (!e.altKey) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    const target = e.target instanceof Element ? e.target : (e.target as Node | null)?.parentElement;
    if (!target) return;
    void resolve(target).then((r) => {
      window.__sidecallLastPick = r;
      (window.__sidecallPicks ??= []).push(r);
      console.log('[sidecall] pick', r);
      window.dispatchEvent(new CustomEvent('sidecall:pick', { detail: r }));
    });
  },
  true,
);
