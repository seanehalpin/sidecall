// React 18 and 19 source resolution (SPEC §5.4) via bippy's fiber helpers.
//
//  - 18: `_debugSource` on the nearest fiber that has one (element precision).
//  - 19: `_debugStack` (an Error created at the jsxDEV call site) symbolicated through the dev
//    server's inline source map. React 19 never refreshes `_debugStack` when it reuses a fiber,
//    so after Fast Refresh we prefer the latest element from the parent host fiber's children,
//    and downgrade to component precision when the frame's `?t=` is older than the newest import.
import {
  getDisplayName,
  getFiberFromHostInstance,
  getLatestFiber,
  getRDTHook,
  isCompositeFiber,
  isHostFiber,
  _renderers,
  type Fiber,
} from 'bippy';
import type { SrcKey } from '../anchor';
import { safeSerializeProps } from '../serialize';
import { stripQuery } from '../util';
import { latestHmrT } from './hmr';
import { symbolicate } from './sourcemap';
import { firstUserFrame, frameT, type Frame } from './stack';
import { asProps, type ChainEntry, type Resolved, type ResolveOptions, type SourceLocation } from './types';

type DebugSource = { fileName: string; lineNumber: number; columnNumber?: number };
type FiberX = Fiber & { _debugSource?: DebugSource; _debugStack?: Error; _debugOwner?: FiberX | null };
type ElementLike = { type?: unknown; key?: string | null; _debugStack?: Error };

const FRAGMENT_TAG = 7;
const MAX_CHAIN = 15;

/** Major.minor.patch of the renderer that injected itself into the hook. */
export function reactVersion(): string | null {
  try {
    for (const r of _renderers as Set<{ version?: string }>) if (r.version) return r.version;
    const hook = getRDTHook() as unknown as { renderers?: Map<number, { version?: string }> };
    for (const r of hook.renderers?.values() ?? []) if (r.version) return r.version;
  } catch {
    /* ignore */
  }
  return null;
}

/** Nearest fiber for an element (walking up DOM ancestors), always the latest alternate. */
export function nearestFiber(target: Element): { fiber: FiberX; hops: number } | null {
  let el: Element | null = target;
  let hops = 0;
  while (el) {
    const f = getFiberFromHostInstance(el) as FiberX | null;
    if (f) return { fiber: getLatestFiber(f) as FiberX, hops };
    el = el.parentElement;
    hops++;
  }
  return null;
}

export const hasFiber = (el: Element): boolean => !!getFiberFromHostInstance(el);

/** The freshest `_debugStack` for a fiber (see file header). */
export function freshDebugStack(fiber: FiberX): { stack: Error | undefined; fresh: boolean } {
  const parent = fiber.return as FiberX | null;
  if (parent && (isHostFiber(parent) || parent.tag === FRAGMENT_TAG)) {
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

// Reading Error.stack formats it lazily, which is the expensive part of repeat scanning.
const frameCache = new WeakMap<Error, Frame | null>();
function userFrameOf(stack: Error | undefined): Frame | null {
  if (!stack) return null;
  let f = frameCache.get(stack);
  if (f === undefined) {
    f = firstUserFrame(stack.stack);
    frameCache.set(stack, f);
  }
  return f;
}

/** Cheap synchronous identity of the JSX call site, used for repeat detection. */
export function reactRepeatKey(fiber: FiberX): string | null {
  const s = fiber._debugSource;
  if (s) return `src:${s.fileName}:${s.lineNumber}:${s.columnNumber ?? 0}`;
  const f = userFrameOf(freshDebugStack(fiber).stack);
  if (f) return `stk:${stripQuery(f.fileName)}:${f.lineNumber}:${f.columnNumber}`;
  const owner = fiber._debugOwner;
  return `own:${String(fiber.type)}:${owner ? getDisplayName(owner.type) : ''}`;
}

async function resolveFrame(f: Frame | null): Promise<SrcKey | null> {
  if (!f) return null;
  const pos = await symbolicate(f);
  return pos ? { file: pos.file, line: pos.line, column: pos.column } : null;
}

export interface FiberSource {
  src: SrcKey;
  /** 'component' when the location is only the component's, not this element's. */
  precision: 'element' | 'component';
}

/** Source of a fiber's own JSX call site (React 18 `_debugSource` or React 19 `_debugStack`). */
export async function sourceOfFiber(fiber: FiberX): Promise<FiberSource | null> {
  const s = fiber._debugSource;
  if (s) return { src: { file: s.fileName, line: s.lineNumber, column: s.columnNumber ?? 0 }, precision: 'element' };
  const { stack } = freshDebugStack(fiber);
  const raw = userFrameOf(stack);
  if (!raw) return null;
  const src = await resolveFrame(raw);
  if (!src) return null;
  const stale = frameT(raw.fileName) < latestHmrT(raw.fileName);
  return { src, precision: stale ? 'component' : 'element' };
}

/** Source location of a host element, for anchoring. */
export async function reactSrcKeyOf(el: Element): Promise<SrcKey | null> {
  const f = getFiberFromHostInstance(el) as FiberX | null;
  if (!f) return null;
  return (await sourceOfFiber(getLatestFiber(f) as FiberX))?.src ?? null;
}

/** Count/index among same-tag elements whose fibers share this fiber's call-site key. */
export function reactRepeat(el: Element, fiber: FiberX): { count: number; index: number } | null {
  const key = reactRepeatKey(fiber);
  if (!key) return null;
  let count = 0;
  let index = 0;
  for (const other of Array.from(document.getElementsByTagName(el.tagName))) {
    const f0 = getFiberFromHostInstance(other) as FiberX | null;
    const of = f0 ? (getLatestFiber(f0) as FiberX) : null;
    if (!of || of.stateNode !== other) continue;
    if (reactRepeatKey(of) === key) {
      if (other === el) index = count;
      count++;
    }
  }
  return { count, index };
}

async function buildChain(fiber: FiberX, full: boolean): Promise<ChainEntry[]> {
  const chain: ChainEntry[] = [];
  for (let c = fiber.return as FiberX | null; c; c = c.return as FiberX | null) {
    if (!isCompositeFiber(c)) continue;
    const entry: ChainEntry = {
      name: getDisplayName(c.type) ?? 'Anonymous',
      type: 'component',
      file: null,
      line: null,
      column: null,
    };
    if (full) {
      const s = await sourceOfFiber(c);
      if (s) Object.assign(entry, s.src);
    }
    chain.push(entry);
    if (!full || chain.length >= MAX_CHAIN) break;
  }
  return chain;
}

export async function resolveReact(target: Element, opts: ResolveOptions): Promise<Resolved | null> {
  const found = nearestFiber(target);
  if (!found) return null;
  const { fiber, hops } = found;

  // Source: the nearest fiber at/above the host fiber that has a call site.
  let source: SourceLocation | null = null;
  let f: FiberX | null = fiber;
  let fiberHops = 0;
  while (f) {
    const s = await sourceOfFiber(f);
    if (s) {
      source = {
        ...s.src,
        precision: hops === 0 && fiberHops === 0 ? s.precision : 'component',
      };
      break;
    }
    f = f.return as FiberX | null;
    fiberHops++;
    if (fiberHops > 20) break;
  }

  const chain = await buildChain(fiber, opts.full);
  let owner: FiberX | null = fiber.return as FiberX | null;
  while (owner && !isCompositeFiber(owner)) owner = owner.return as FiberX | null;

  return {
    framework: {
      name: 'react',
      version: reactVersion(),
      source,
      componentChain: chain,
      props: asProps(opts.full && owner ? safeSerializeProps(owner.memoizedProps) : null),
    },
    repeat: opts.full ? reactRepeat(target, fiber) : null,
    componentName: chain[0]?.name ?? null,
  };
}
