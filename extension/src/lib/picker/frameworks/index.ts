// Framework detection and source resolution (SPEC §5.4): Svelte 5, React 18/19, else unknown.
import { asProps, type Resolved, type ResolveOptions } from './types';
import { findMetaElement, hasSvelteRuntime, resolveSvelte, svelteSrcKeyOf } from './svelte';
import { hasFiber, reactSrcKeyOf, resolveReact } from './react';
import type { SrcKey } from '../anchor';

export type { Resolved, ResolveOptions } from './types';

function unknown(): Resolved {
  return {
    framework: { name: 'unknown', version: null, source: null, componentChain: [], props: asProps(null) },
    repeat: null,
    componentName: null,
  };
}

/** Never throws: a failure degrades to "unknown". */
export async function resolveFramework(target: Element, opts: ResolveOptions): Promise<Resolved> {
  try {
    if (findMetaElement(target)) return resolveSvelte(target, opts) ?? unknown();
    if (hasFiber(target) || target.parentElement) {
      const r = await resolveReact(target, opts);
      if (r) return r;
    }
    if (hasSvelteRuntime()) return resolveSvelte(target, opts) ?? unknown();
  } catch (err) {
    console.debug('[sidecall] source resolution failed', err);
  }
  return unknown();
}

/** Source location used to re-find an element (raw framework values). */
export async function srcKeyOf(el: Element): Promise<SrcKey | null> {
  try {
    return svelteSrcKeyOf(el) ?? (await reactSrcKeyOf(el));
  } catch {
    return null;
  }
}
