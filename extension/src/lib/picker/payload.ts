// Builds a PickDraft and Anchor (SPEC §5.3) from an element or a region. Framework values are
// sent raw (paths and columns are normalised by the daemon).
import { routeOf } from '../page-context';
import type { Anchor, PickDraft, ViewportInfo } from '../picker-protocol';
import type { ElementInfo, PageInfo, Rect } from '../protocol.gen';
import { errorBuffer } from './console-capture';
import { resolveFramework, srcKeyOf, type Resolved } from './frameworks';
import { asProps } from './frameworks/types';
import { collapsedOuterHTML } from './html';
import { rankRegionElements } from './region';
import { buildSelectors, primarySelector } from './selector';
import { curateStyles } from './styles';
import { OVERLAY_HOST_ID, rectOf, textFingerprint, trimText } from './util';

const MAX_SCAN = 4000;
const SKIP_TAGS = new Set(['html', 'head', 'script', 'style', 'link', 'meta', 'noscript', 'title', 'template']);

export function viewportInfo(): ViewportInfo {
  return { w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio || 1 };
}

export function pageInfo(): PageInfo {
  let dark = false;
  try {
    dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    /* ignore */
  }
  return {
    url: location.href,
    route: routeOf(location.href) ?? location.pathname,
    title: document.title,
    viewport: viewportInfo(),
    scroll: { x: Math.round(window.scrollX), y: Math.round(window.scrollY) },
    colorScheme: dark ? 'dark' : 'light',
    consoleErrors: errorBuffer.toArray(),
  };
}

export function elementText(el: Element): string {
  return el.textContent ?? '';
}

export function elementInfo(el: Element): ElementInfo {
  const cs = getComputedStyle(el);
  const attributes: Record<string, string> = {};
  for (const a of Array.from(el.attributes).slice(0, 30)) {
    attributes[a.name] = a.value.length > 200 ? `${a.value.slice(0, 200)}…` : a.value;
  }
  const { selector, altSelectors } = buildSelectors(el);
  return {
    selector,
    altSelectors,
    tag: el.tagName.toLowerCase(),
    attributes,
    text: trimText(elementText(el), 500),
    outerHTML: collapsedOuterHTML(el),
    rect: rectOf(el.getBoundingClientRect()),
    computedStyles: curateStyles((p) => cs.getPropertyValue(p)),
  };
}

export function buildAnchor(el: Element, info: Pick<ElementInfo, 'selector' | 'altSelectors'>, r: Resolved): Anchor {
  const s = r.framework.source;
  return {
    tag: el.tagName.toLowerCase(),
    source: s ? { file: s.file, line: s.line, column: s.column } : null,
    selector: info.selector,
    altSelectors: info.altSelectors,
    textFingerprint: textFingerprint(elementText(el)),
    repeatIndex: r.repeat?.index ?? null,
    region: null,
  };
}

/** Rebuild an anchor for an element that was re-found (selector and source may have moved). */
export async function reanchorFor(el: Element, prev: Anchor): Promise<Anchor> {
  const { selector, altSelectors } = buildSelectors(el);
  const src = await srcKeyOf(el);
  return {
    ...prev,
    selector,
    altSelectors,
    source: src ?? prev.source,
    textFingerprint: textFingerprint(elementText(el)) || prev.textFingerprint,
    region: null,
  };
}

export interface BuiltPick {
  draft: PickDraft;
  anchor: Anchor;
  rect: Rect;
  viewport: ViewportInfo;
}

export async function buildElementPick(el: Element): Promise<BuiltPick> {
  const resolved = await resolveFramework(el, { full: true });
  const element = elementInfo(el);
  const draft: PickDraft = {
    kind: 'element',
    page: pageInfo(),
    element,
    region: null,
    framework: resolved.framework,
    repeat: resolved.repeat,
  };
  return { draft, anchor: buildAnchor(el, element, resolved), rect: element.rect, viewport: viewportInfo() };
}

/** Visible elements under a viewport rect, ranked and cut to 20. */
export function elementsInRegion(rect: Rect): Element[] {
  const cands: { item: Element; rect: Rect }[] = [];
  const all = document.body ? document.body.getElementsByTagName('*') : [];
  const host = document.getElementById(OVERLAY_HOST_ID);
  for (let i = 0; i < all.length && i < MAX_SCAN; i++) {
    const el = all[i]!;
    if (SKIP_TAGS.has(el.tagName.toLowerCase()) || el === host) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    cands.push({ item: el, rect: rectOf(r) });
  }
  return rankRegionElements(rect, cands).map((c) => c.item);
}

export async function buildRegionPick(rect: Rect): Promise<BuiltPick> {
  const els = elementsInRegion(rect);
  const elements = els.map((el) => ({
    selector: primarySelector(el),
    tag: el.tagName.toLowerCase(),
    text: trimText(elementText(el), 100),
    rect: rectOf(el.getBoundingClientRect()),
  }));
  // Name/version of the framework only; a region has no single source location.
  const probe = els[0] ? await resolveFramework(els[0], { full: false }) : null;
  const draft: PickDraft = {
    kind: 'region',
    page: pageInfo(),
    element: null,
    region: { rect, elements },
    framework: {
      name: probe?.framework.name ?? 'unknown',
      version: probe?.framework.version ?? null,
      source: null,
      componentChain: [],
      props: asProps(null),
    },
    repeat: null,
  };
  const anchor: Anchor = {
    tag: '',
    source: null,
    selector: '',
    altSelectors: [],
    textFingerprint: '',
    repeatIndex: null,
    // Document coordinates, so the pin follows the page when it scrolls.
    region: { x: rect.x + window.scrollX, y: rect.y + window.scrollY, w: rect.w, h: rect.h },
  };
  return { draft, anchor, rect, viewport: viewportInfo() };
}
