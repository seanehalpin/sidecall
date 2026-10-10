// Re-anchoring (SPEC §5.2): find a picked element again after an HMR update, DOM change or
// reload. Pure logic over a small environment interface, so it is unit-testable without a browser.
//
//   1. the element we already hold, if it is still connected and still matches
//   2. an element at the same source location (+ repeatIndex among those sharing it)
//   3. the unique selector (then the alternates)
//   4. a text fingerprint among same-tag elements
//   5. stale
import type { Anchor } from '../picker-protocol';
import { stripQuery } from './util';

export interface SrcKey {
  file: string;
  line: number;
  column: number;
}

export interface AnchorEnv<E> {
  isConnected(e: E): boolean;
  tagOf(e: E): string;
  /** Normalised leading text (see textFingerprint). */
  textOf(e: E): string;
  /** Resolved source location of an element, if the framework exposes one. */
  sourceOf(e: E): Promise<SrcKey | null>;
  queryAll(selector: string): E[];
  /** Same-tag elements in document order. */
  byTag(tag: string): E[];
}

export type Resolution<E> =
  | { status: 'ok'; el: E | null; how: 'kept' | 'source' | 'selector' | 'text' | 'region' }
  | { status: 'stale' };

const MAX_SOURCE_CANDIDATES = 500;

export const sameFile = (a: string, b: string): boolean => stripQuery(a) === stripQuery(b);

export function sameSource(a: SrcKey, b: SrcKey): boolean {
  return sameFile(a.file, b.file) && a.line === b.line && a.column === b.column;
}

/** Pick one of several same-source candidates: repeatIndex first, text to break ties. */
export function chooseCandidate<E>(
  list: E[],
  repeatIndex: number | null,
  fingerprint: string,
  textOf: (e: E) => string,
): E | null {
  if (list.length === 0) return null;
  if (list.length === 1) return list[0]!;
  const byIdx = repeatIndex != null ? list[repeatIndex] : undefined;
  if (byIdx && (!fingerprint || textOf(byIdx) === fingerprint)) return byIdx;
  const byText = fingerprint ? list.find((e) => textOf(e) === fingerprint) : undefined;
  return byText ?? byIdx ?? list[0]!;
}

export async function reanchor<E>(anchor: Anchor, current: E | null, env: AnchorEnv<E>): Promise<Resolution<E>> {
  // Region picks have no element: the rect is the anchor.
  if (anchor.region) return { status: 'ok', el: null, how: 'region' };

  // 1. Keep the current element while it still looks like itself.
  if (current && env.isConnected(current) && env.tagOf(current) === anchor.tag) {
    if (!anchor.textFingerprint || env.textOf(current) === anchor.textFingerprint) {
      return { status: 'ok', el: current, how: 'kept' };
    }
    if (env.queryAll(anchor.selector).includes(current)) return { status: 'ok', el: current, how: 'kept' };
    if (anchor.source) {
      const s = await env.sourceOf(current);
      if (s && sameSource(s, anchor.source)) return { status: 'ok', el: current, how: 'kept' };
    }
  }

  // 2. Same source location. Exact first; then the same file/tag/column with a shifted line
  //    (an edit above the element moves every line but not its column).
  if (anchor.source) {
    const src = anchor.source;
    const cands = env.byTag(anchor.tag).slice(0, MAX_SOURCE_CANDIDATES);
    const srcs = await Promise.all(cands.map((c) => env.sourceOf(c)));
    const exact: E[] = [];
    const loose: E[] = [];
    cands.forEach((c, i) => {
      const s = srcs[i];
      if (!s || !sameFile(s.file, src.file)) return;
      if (s.line === src.line && s.column === src.column) exact.push(c);
      else if (s.column === src.column) loose.push(c);
    });
    const pick = chooseCandidate(exact, anchor.repeatIndex, anchor.textFingerprint, env.textOf);
    if (pick) return { status: 'ok', el: pick, how: 'source' };
    // Loose matches are only trusted when text agrees or there is a single candidate.
    if (loose.length === 1) return { status: 'ok', el: loose[0]!, how: 'source' };
    const looseText = anchor.textFingerprint ? loose.filter((e) => env.textOf(e) === anchor.textFingerprint) : [];
    const lp = chooseCandidate(looseText, anchor.repeatIndex, anchor.textFingerprint, env.textOf);
    if (lp) return { status: 'ok', el: lp, how: 'source' };
  }

  // 3. Unique selector, then the alternates.
  for (const sel of [anchor.selector, ...anchor.altSelectors]) {
    let found: E[] = [];
    try {
      found = env.queryAll(sel);
    } catch {
      continue;
    }
    if (found.length === 1 && env.tagOf(found[0]!) === anchor.tag) return { status: 'ok', el: found[0]!, how: 'selector' };
  }

  // 4. Text fingerprint among same-tag elements.
  if (anchor.textFingerprint) {
    const same = env.byTag(anchor.tag).filter((e) => env.textOf(e) === anchor.textFingerprint);
    const pick = chooseCandidate(same, anchor.repeatIndex, anchor.textFingerprint, env.textOf);
    if (pick) return { status: 'ok', el: pick, how: 'text' };
  }

  return { status: 'stale' };
}
