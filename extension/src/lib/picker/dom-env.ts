// AnchorEnv backed by the live document.
import type { AnchorEnv } from './anchor';
import { srcKeyOf } from './frameworks';
import { textFingerprint } from './util';

export const domEnv: AnchorEnv<Element> = {
  isConnected: (e) => e.isConnected,
  tagOf: (e) => e.tagName.toLowerCase(),
  textOf: (e) => textFingerprint(e.textContent ?? ''),
  sourceOf: (e) => srcKeyOf(e),
  queryAll: (sel) => {
    try {
      return Array.from(document.querySelectorAll(sel));
    } catch {
      return [];
    }
  },
  byTag: (tag) => (tag ? Array.from(document.getElementsByTagName(tag)) : []),
};
