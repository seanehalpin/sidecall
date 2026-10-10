// Svelte 5 source resolution (SPEC §5.4), dev builds only: `el.__svelte_meta`.
import type { SrcKey } from '../anchor';
import { asProps, type ChainEntry, type Resolved, type ResolveOptions, type SourceLocation } from './types';

interface SvelteStackEntry {
  type: string;
  file?: string;
  line?: number;
  column?: number;
  componentTag?: string;
  parent: SvelteStackEntry | null;
}
interface SvelteMeta {
  loc?: { file: string; line: number; column: number };
  parent?: SvelteStackEntry | null;
}

const metaOf = (el: Element): SvelteMeta | null | undefined =>
  (el as unknown as { __svelte_meta?: SvelteMeta | null }).__svelte_meta;

/** Only the major version is exposed at runtime ("5"). */
export function svelteVersion(): string | null {
  const v = window.__svelte?.v;
  if (!v || typeof v[Symbol.iterator] !== 'function') return null;
  const [first] = v;
  return first ? String(first) : null;
}

export function hasSvelteRuntime(): boolean {
  return !!window.__svelte;
}

/** The element carrying `__svelte_meta.loc`: the target itself, else the nearest ancestor. */
export function findMetaElement(target: Element): { el: Element; hops: number } | null {
  let el: Element | null = target;
  let hops = 0;
  while (el) {
    if (metaOf(el)?.loc) return { el, hops };
    el = el.parentElement;
    hops++;
  }
  return null;
}

export const svelteSrcKeyOf = (el: Element): SrcKey | null => {
  const loc = metaOf(el)?.loc;
  return loc ? { file: loc.file, line: loc.line, column: loc.column } : null;
};

/** Chain entries point at where the tag/block is written (the parent file). */
export function svelteChain(meta: SvelteMeta): ChainEntry[] {
  const chain: ChainEntry[] = [];
  for (let e = meta.parent ?? null; e; e = e.parent) {
    if (e.file?.includes('node_modules/')) continue; // SvelteKit's own root/layout
    chain.push({
      name: e.type === 'component' ? (e.componentTag ?? 'component') : `{#${e.type}}`,
      type: e.type,
      file: e.file ?? null,
      line: e.line ?? null,
      column: e.column ?? null,
    });
    if (chain.length >= 15) break;
  }
  return chain;
}

const keyOf = (l: { file: string; line: number; column: number }) => `${l.file}:${l.line}:${l.column}`;

/** Count and index among the elements that share this element's source location. */
export function svelteRepeat(metaEl: Element): { count: number; index: number } {
  const key = keyOf(metaOf(metaEl)!.loc!);
  let count = 0;
  let index = 0;
  for (const other of Array.from(document.getElementsByTagName(metaEl.tagName))) {
    const l = metaOf(other)?.loc;
    if (l && keyOf(l) === key) {
      if (other === metaEl) index = count;
      count++;
    }
  }
  return { count, index };
}

export function resolveSvelte(target: Element, opts: ResolveOptions): Resolved | null {
  const found = findMetaElement(target);
  const version = svelteVersion();
  if (!found) {
    if (!hasSvelteRuntime()) return null;
    return {
      framework: { name: 'svelte', version, source: null, componentChain: [], props: asProps(null) },
      repeat: null,
      componentName: null,
    };
  }
  const meta = metaOf(found.el)!;
  const loc = meta.loc!;
  const source: SourceLocation = {
    file: loc.file,
    line: loc.line,
    column: loc.column,
    precision: found.hops === 0 ? 'element' : 'component',
  };
  const chain = svelteChain(meta);
  const componentName = chain.find((c) => c.type === 'component')?.name ?? null;
  return {
    framework: {
      name: 'svelte',
      version,
      source,
      componentChain: opts.full ? chain : chain.slice(0, 1),
      props: asProps(null),
    },
    repeat: opts.full ? svelteRepeat(found.el) : null,
    componentName,
  };
}
