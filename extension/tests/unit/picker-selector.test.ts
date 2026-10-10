// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest';
import { buildSelectors, isStableToken } from '../../src/lib/picker/selector';

function setup(html: string): Document {
  document.body.innerHTML = html;
  return document;
}
const verify = (el: Element) => {
  const { selector, altSelectors } = buildSelectors(el);
  for (const s of [selector, ...altSelectors]) {
    const found = document.querySelectorAll(s);
    expect(found.length, s).toBe(1);
    expect(found[0], s).toBe(el);
  }
  return { selector, altSelectors };
};

describe('buildSelectors', () => {
  beforeEach(() => setup(''));

  it('prefers a stable #id', () => {
    setup('<div><button id="save">Save</button><button>x</button></div>');
    expect(verify(document.getElementById('save')!).selector).toBe('#save');
  });

  it('then data-testid', () => {
    setup('<div><button data-testid="go">Go</button><button>x</button></div>');
    expect(verify(document.querySelector('[data-testid=go]')!).selector).toBe('[data-testid="go"]');
  });

  it('uses stable classes when unique', () => {
    setup('<ul><li class="item svelte-abc123">a</li><li class="item other">b</li></ul>');
    const el = document.querySelector('.other')!;
    expect(verify(el).selector).toBe('li.item.other');
  });

  it('falls back to an nth-of-type path for repeated siblings', () => {
    setup('<main><article class="card"><h2>1</h2></article><article class="card"><h2>2</h2></article></main>');
    const h2 = document.querySelectorAll('h2')[1]!;
    const { selector } = verify(h2);
    expect(selector).toContain('nth-of-type(2)');
  });

  it('ignores generated ids and classes', () => {
    setup('<div id=":r1:" class="css-1a2b3c"><span>x</span></div><div>y</div>');
    const el = document.querySelector('span')!;
    const { selector } = verify(el);
    expect(selector).not.toContain(':r1:');
    expect(selector).not.toContain('css-');
  });

  it('gives alternates that also resolve uniquely (max 3)', () => {
    setup('<div id="a" data-testid="t" class="box"><p>x</p></div>');
    const r = verify(document.getElementById('a')!);
    expect(r.altSelectors.length).toBeGreaterThan(0);
    expect(r.altSelectors.length).toBeLessThanOrEqual(3);
  });
});

describe('isStableToken', () => {
  it.each([
    ['card', true],
    ['card-title', true],
    ['svelte-1x2y3z', false],
    ['css-abc123', false],
    ['_a1b2c3d4', false],
    [':r1:', false],
    ['abcdef12345', false],
    ['', false],
  ])('%s → %s', (t, ok) => expect(isStableToken(t)).toBe(ok));
});
