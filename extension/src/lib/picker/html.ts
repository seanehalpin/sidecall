// outerHTML for the payload: children collapsed beyond depth 3, trimmed to 4 KB.
import { collapseWs } from './util';

export const OUTER_HTML_MAX = 4096;
const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);
const SKIP = new Set(['script', 'style', 'noscript', 'template']);
const MAX_ATTR = 120;
const MAX_TEXT = 200;

const escText = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const escAttr = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;');

function openTag(el: Element): string {
  let s = `<${el.tagName.toLowerCase()}`;
  for (const a of Array.from(el.attributes)) {
    const v = a.value.length > MAX_ATTR ? `${a.value.slice(0, MAX_ATTR)}…` : a.value;
    s += a.value === '' ? ` ${a.name}` : ` ${a.name}="${escAttr(v)}"`;
  }
  return `${s}>`;
}

function render(el: Element, depth: number, maxDepth: number): string {
  const tag = el.tagName.toLowerCase();
  const open = openTag(el);
  if (VOID.has(tag)) return open;
  let inner = '';
  let collapsed = 0;
  for (const n of Array.from(el.childNodes)) {
    if (n.nodeType === 3) {
      const t = collapseWs(n.textContent ?? '');
      if (t) inner += escText(t.length > MAX_TEXT ? `${t.slice(0, MAX_TEXT)}…` : t);
    } else if (n.nodeType === 1) {
      const c = n as Element;
      if (SKIP.has(c.tagName.toLowerCase())) continue;
      if (depth >= maxDepth) collapsed++;
      else inner += render(c, depth + 1, maxDepth);
    }
  }
  if (collapsed) inner += `<!-- ${collapsed} child element${collapsed === 1 ? '' : 's'} collapsed -->`;
  return `${open}${inner}</${tag}>`;
}

/**
 * Serialise `el`, collapsing element children deeper than `maxDepth` (root is depth 0).
 * If the result is still over `maxBytes`, retry with a shallower cut, then hard-truncate.
 */
export function collapsedOuterHTML(el: Element, maxDepth = 3, maxBytes = OUTER_HTML_MAX): string {
  for (let d = maxDepth; d >= 0; d--) {
    const s = render(el, 0, d);
    if (s.length <= maxBytes) return s;
  }
  const s = render(el, 0, 0);
  return `${s.slice(0, maxBytes - 1)}…`;
}
