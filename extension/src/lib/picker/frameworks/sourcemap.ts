// Resolve a frame in a Vite-served (transformed) module back to the original file:line via the
// module's inline source map. Cached per module URL (including ?t=, since each HMR version is
// different code), so repeated picks cost ~1 ms.
import { SourceMapConsumer } from 'source-map-js';
import type { Frame } from './stack';
import { stripQuery } from '../util';

const MAX_CACHED = 64;
const maps = new Map<string, Promise<SourceMapConsumer | null>>();

function load(moduleUrl: string): Promise<SourceMapConsumer | null> {
  let p = maps.get(moduleUrl);
  if (p) return p;
  p = (async () => {
    const res = await fetch(moduleUrl);
    if (!res.ok) return null;
    const code = await res.text();
    const m = /\/\/# sourceMappingURL=(\S+)\s*$/.exec(code);
    if (!m) return null;
    let raw: string;
    if (m[1]!.startsWith('data:')) {
      const b64 = m[1]!.slice(m[1]!.indexOf(',') + 1);
      raw = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)));
    } else {
      raw = await (await fetch(new URL(m[1]!, moduleUrl))).text();
    }
    return new SourceMapConsumer(JSON.parse(raw));
  })().catch(() => null);
  maps.set(moduleUrl, p);
  if (maps.size > MAX_CACHED) maps.delete(maps.keys().next().value as string);
  return p;
}

export interface OriginalPosition {
  file: string;
  line: number;
  /** 0-based, as source maps report it. */
  column: number;
}

const resolved = new Map<string, Promise<OriginalPosition | null>>();

export function symbolicate(frame: Frame): Promise<OriginalPosition | null> {
  const key = `${frame.fileName}:${frame.lineNumber}:${frame.columnNumber}`;
  let p = resolved.get(key);
  if (p) return p;
  p = (async () => {
    const consumer = await load(frame.fileName);
    if (!consumer) return null;
    const pos = consumer.originalPositionFor({ line: frame.lineNumber, column: frame.columnNumber - 1 });
    if (!pos.source || pos.line == null) return null;
    // Vite lists `sources` relative to the module URL (often a bare basename): resolve it.
    let file = pos.source;
    try {
      file = new URL(pos.source, stripQuery(frame.fileName)).href;
    } catch {
      /* keep as-is */
    }
    return { file, line: pos.line, column: pos.column ?? 0 };
  })();
  resolved.set(key, p);
  if (resolved.size > 500) resolved.delete(resolved.keys().next().value as string);
  return p;
}
