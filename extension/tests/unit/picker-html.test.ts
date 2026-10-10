// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { collapsedOuterHTML, OUTER_HTML_MAX } from '../../src/lib/picker/html';

const el = (html: string): Element => {
  document.body.innerHTML = html;
  return document.body.firstElementChild!;
};

describe('collapsedOuterHTML', () => {
  it('serialises small trees fully, collapsing whitespace', () => {
    const e = el('<div class="a"> <p>Hello   <b>world</b></p> <br> </div>');
    expect(collapsedOuterHTML(e)).toBe('<div class="a"><p>Hello<b>world</b></p><br></div>');
  });

  it('collapses children beyond depth 3', () => {
    const e = el('<d0><d1><d2><d3><d4>deep</d4></d3></d2></d1></d0>');
    const out = collapsedOuterHTML(e, 3);
    expect(out).toContain('<d3>');
    expect(out).not.toContain('<d4>');
    expect(out).toContain('1 child element collapsed');
  });

  it('drops script/style and escapes text and attributes', () => {
    const e = el('<div title=\'a"b\'><script>x()</script><style>p{}</style>1 &lt; 2</div>');
    const out = collapsedOuterHTML(e);
    expect(out).toBe('<div title="a&quot;b">1 &lt; 2</div>');
  });

  it('stays within the byte cap, trading depth for size first', () => {
    const rows = Array.from({ length: 400 }, (_, i) => `<li><span>row ${i}</span></li>`).join('');
    const e = el(`<ul>${rows}</ul>`);
    const out = collapsedOuterHTML(e);
    expect(out.length).toBeLessThanOrEqual(OUTER_HTML_MAX);
    expect(out.startsWith('<ul>')).toBe(true);
  });
});
