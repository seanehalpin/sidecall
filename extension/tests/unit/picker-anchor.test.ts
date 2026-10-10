import { describe, expect, it } from 'vitest';
import type { Anchor } from '../../src/lib/picker-protocol';
import { chooseCandidate, reanchor, sameSource, type AnchorEnv, type SrcKey } from '../../src/lib/picker/anchor';

interface N {
  name: string;
  tag: string;
  text: string;
  src: SrcKey | null;
  connected: boolean;
  selectors?: string[];
}

function envFor(nodes: N[]): AnchorEnv<N> {
  return {
    isConnected: (n) => n.connected,
    tagOf: (n) => n.tag,
    textOf: (n) => n.text,
    sourceOf: async (n) => n.src,
    queryAll: (sel) => nodes.filter((n) => n.connected && n.selectors?.includes(sel)),
    byTag: (tag) => nodes.filter((n) => n.connected && n.tag === tag),
  };
}

const src = (line: number, column = 2, file = 'src/Card.svelte'): SrcKey => ({ file, line, column });
const mk = (name: string, text: string, s: SrcKey | null, extra: Partial<N> = {}): N => ({
  name, tag: 'h2', text, src: s, connected: true, ...extra,
});
const anchor = (over: Partial<Anchor> = {}): Anchor => ({
  tag: 'h2', source: src(8), selector: 'h2.t', altSelectors: [], textFingerprint: 'project 3', repeatIndex: 2, region: null, ...over,
});

describe('reanchor', () => {
  it('1. keeps a connected element whose text still matches', async () => {
    const a = mk('a', 'project 3', src(8));
    const r = await reanchor(anchor(), a, envFor([a]));
    expect(r).toEqual({ status: 'ok', el: a, how: 'kept' });
  });

  it('1. keeps the element when text changed but the selector still resolves to it', async () => {
    const a = mk('a', 'changed', src(8), { selectors: ['h2.t'] });
    expect((await reanchor(anchor(), a, envFor([a]))).status).toBe('ok');
  });

  it('1. keeps the element when text changed but its source is unchanged', async () => {
    const a = mk('a', 'changed', src(8));
    const r = await reanchor(anchor(), a, envFor([a]));
    expect(r).toMatchObject({ status: 'ok', how: 'kept' });
  });

  it('2. finds a rebuilt element by exact source, using repeatIndex among siblings', async () => {
    const list = [0, 1, 2, 3].map((i) => mk(`n${i}`, `project ${i + 1}`, src(8)));
    const old = mk('old', 'project 3', src(8), { connected: false });
    const r = await reanchor(anchor({ textFingerprint: '' }), old, envFor(list));
    expect(r).toMatchObject({ status: 'ok', how: 'source' });
    expect((r as { el: N }).el.name).toBe('n2');
  });

  it('2. follows a line shift (edit above the element) via file+column+text', async () => {
    const list = [0, 1, 2, 3].map((i) => mk(`n${i}`, `project ${i + 1}`, src(9)));
    const r = await reanchor(anchor(), null, envFor(list));
    expect(r).toMatchObject({ status: 'ok', how: 'source' });
    expect((r as { el: N }).el.name).toBe('n2');
  });

  it('2. a single shifted candidate is accepted even if its text changed', async () => {
    const only = mk('only', 'something else', src(9));
    expect((await reanchor(anchor(), null, envFor([only]))).status).toBe('ok');
  });

  it('2. ignores ?t= query when comparing files', () => {
    expect(sameSource(src(1, 1, 'http://x/Card.tsx?t=5'), src(1, 1, 'http://x/Card.tsx?t=9'))).toBe(true);
    expect(sameSource(src(1, 1, 'a.tsx'), src(1, 1, 'b.tsx'))).toBe(false);
  });

  it('3. falls back to a unique selector when there is no source', async () => {
    const a = mk('a', 'whatever', null, { selectors: ['main > h2'] });
    const r = await reanchor(anchor({ source: null, selector: 'main > h2', textFingerprint: 'other' }), null, envFor([a]));
    expect(r).toMatchObject({ status: 'ok', how: 'selector' });
  });

  it('3. rejects an ambiguous selector and one that lands on the wrong tag', async () => {
    const a = mk('a', 'x', null, { selectors: ['s'] });
    const b = mk('b', 'y', null, { selectors: ['s'] });
    expect((await reanchor(anchor({ source: null, selector: 's', textFingerprint: '' }), null, envFor([a, b]))).status).toBe('stale');
    const p = mk('p', 'x', null, { tag: 'p', selectors: ['q'] });
    expect((await reanchor(anchor({ source: null, selector: 'q', textFingerprint: '' }), null, envFor([p]))).status).toBe('stale');
  });

  it('3. tries alternates', async () => {
    const a = mk('a', 'x', null, { selectors: ['#alt'] });
    const r = await reanchor(anchor({ source: null, selector: '.gone', altSelectors: ['#alt'], textFingerprint: '' }), null, envFor([a]));
    expect(r).toMatchObject({ status: 'ok', how: 'selector' });
  });

  it('4. matches by text fingerprint among same-tag elements, honouring repeatIndex', async () => {
    const dupes = [mk('a', 'same', null), mk('b', 'same', null), mk('c', 'other', null)];
    const r = await reanchor(anchor({ source: null, selector: '.gone', textFingerprint: 'same', repeatIndex: 1 }), null, envFor(dupes));
    expect(r).toMatchObject({ status: 'ok', how: 'text' });
    expect((r as { el: N }).el.name).toBe('b');
  });

  it('5. is stale when nothing matches', async () => {
    const x = mk('x', 'unrelated', src(50, 9));
    expect(await reanchor(anchor(), null, envFor([x]))).toEqual({ status: 'stale' });
  });

  it('a disconnected current element is not kept', async () => {
    const old = mk('old', 'project 3', src(8), { connected: false });
    expect((await reanchor(anchor(), old, envFor([old]))).status).toBe('stale');
  });

  it('region anchors are always ok with no element', async () => {
    const r = await reanchor(anchor({ region: { x: 0, y: 0, w: 5, h: 5 }, tag: '' }), null, envFor([]));
    expect(r).toEqual({ status: 'ok', el: null, how: 'region' });
  });
});

describe('chooseCandidate', () => {
  const text = (s: string) => s;
  it('returns null/only/by index/by text', () => {
    expect(chooseCandidate([], 0, '', text)).toBeNull();
    expect(chooseCandidate(['a'], 5, '', text)).toBe('a');
    expect(chooseCandidate(['a', 'b', 'c'], 1, '', text)).toBe('b');
    expect(chooseCandidate(['a', 'b', 'c'], 0, 'c', text)).toBe('c');
    expect(chooseCandidate(['a', 'b'], null, '', text)).toBe('a');
  });
});
