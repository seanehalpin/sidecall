// Unique CSS selectors for an element (SPEC §5.3): prefers #id, data-testid, stable classes,
// then an nth-of-type path. Every returned selector is verified to match exactly this element.

const MAX_ALT = 3;

export function cssEscape(s: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(s);
  return s.replace(/[^a-zA-Z0-9_\u00A0-\uFFFF-]/g, (c) => `\\${c}`).replace(/^(\d)/, '\\3$1 ');
}

/** Ids/classes that look generated (hashes, framework scoping, CSS-in-JS) are unstable. */
export function isStableToken(t: string): boolean {
  if (!t || t.length > 40) return false;
  if (/^svelte-[a-z0-9]+$/i.test(t)) return false;
  if (/^(css|sc|jsx|emotion|styled)-/i.test(t)) return false;
  if (/^_/.test(t)) return false;
  if (/[a-z]+[-_][a-z0-9]{5,8}$/i.test(t) && /\d/.test(t.slice(-6)) && /[a-z]/i.test(t.slice(-6))) return false;
  if (/^:.*:$/.test(t)) return false; // React useId
  if (/\d{4,}/.test(t)) return false;
  if (/^[a-z0-9]{8,}$/i.test(t) && /\d/.test(t) && /[a-z]/i.test(t)) return false;
  return true;
}

function stableClasses(el: Element): string[] {
  return Array.from(el.classList).filter(isStableToken);
}

function isUnique(root: ParentNode, sel: string, el: Element): boolean {
  try {
    const found = root.querySelectorAll(sel);
    return found.length === 1 && found[0] === el;
  } catch {
    return false;
  }
}

function tagOf(el: Element): string {
  return el.tagName.toLowerCase();
}

const idSel = (el: Element) => (el.id && isStableToken(el.id) ? `#${cssEscape(el.id)}` : null);
const testSel = (el: Element) => {
  const v = el.getAttribute('data-testid');
  return v ? `[data-testid="${v.replace(/["\\]/g, '\\$&')}"]` : null;
};
const classSel = (el: Element, max = 2) => {
  const cls = stableClasses(el).slice(0, max);
  return cls.length ? `${tagOf(el)}${cls.map((c) => `.${cssEscape(c)}`).join('')}` : null;
};

function nth(el: Element): string {
  const parent = el.parentElement;
  if (!parent) return tagOf(el);
  const same = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
  return same.length === 1 ? tagOf(el) : `${tagOf(el)}:nth-of-type(${same.indexOf(el) + 1})`;
}

/** Path from `el` upward until it is unique; `segment` decides how each ancestor is written. */
function pathSelector(
  root: ParentNode,
  el: Element,
  segment: (e: Element) => string,
  stopAt: (e: Element) => string | null,
): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur !== (root as Document).documentElement) {
    const anchor = cur === el ? null : stopAt(cur);
    if (anchor) {
      parts.unshift(anchor);
      const sel = parts.join(' > ');
      return sel; // callers verify uniqueness; an anchored path is as good as it gets
    }
    parts.unshift(segment(cur));
    const sel = parts.join(' > ');
    if (isUnique(root, sel, el)) return sel;
    cur = cur.parentElement;
  }
  return parts.join(' > ');
}

function absolutePath(root: ParentNode, el: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur) {
    parts.unshift(cur === (root as Document).documentElement ? tagOf(cur) : nth(cur));
    if (cur === (root as Document).documentElement) break;
    cur = cur.parentElement;
  }
  return parts.join(' > ');
}

export interface SelectorSet {
  selector: string;
  altSelectors: string[];
}

export function buildSelectors(el: Element, root: ParentNode = el.ownerDocument): SelectorSet {
  const candidates: string[] = [];
  const add = (s: string | null) => {
    if (s && !candidates.includes(s) && isUnique(root, s, el)) candidates.push(s);
  };
  add(idSel(el));
  add(testSel(el));
  add(classSel(el, 1));
  add(classSel(el, 3));
  add(pathSelector(root, el, (e) => classSel(e, 2) ?? nth(e), (e) => idSel(e) ?? testSel(e)));
  add(pathSelector(root, el, nth, (e) => idSel(e) ?? testSel(e)));
  add(absolutePath(root, el));
  if (candidates.length === 0) candidates.push(absolutePath(root, el));
  const [selector, ...rest] = candidates;
  return { selector: selector!, altSelectors: rest.slice(0, MAX_ALT) };
}

/** Selector for a lightweight summary (region element lists): only the primary one. */
export function primarySelector(el: Element, root: ParentNode = el.ownerDocument): string {
  return buildSelectors(el, root).selector;
}
