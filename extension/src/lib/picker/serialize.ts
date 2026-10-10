// Safe props serialiser (SPEC §5.3): depth 3, 4 KB cap, functions → "[fn name]", cycles guarded.

export const PROPS_MAX_BYTES = 4096;
const MAX_DEPTH = 3;
const MAX_KEYS = 30;
const MAX_ITEMS = 20;
const MAX_STRING = 200;

export function serializeValue(v: unknown, depth: number, seen: WeakSet<object>): unknown {
  switch (typeof v) {
    case 'string':
      return v.length > MAX_STRING ? `${v.slice(0, MAX_STRING)}…` : v;
    case 'number':
      return Number.isFinite(v) ? v : String(v);
    case 'boolean':
    case 'undefined':
      return v === undefined ? '[undefined]' : v;
    case 'bigint':
      return `${v}n`;
    case 'symbol':
      return String(v);
    case 'function':
      return `[fn ${v.name || 'anonymous'}]`;
  }
  if (v === null) return null;
  const o = v as object;
  try {
    if (seen.has(o)) return '[Circular]';
    if (typeof Node !== 'undefined' && o instanceof Node) return `[${o.nodeName.toLowerCase()}]`;
    if (o instanceof Date) return o.toISOString();
    if (o instanceof Error) return `[${o.name}: ${o.message}]`;
    if ((o as { $$typeof?: unknown }).$$typeof) {
      const t = (o as { type?: unknown }).type;
      return `[ReactElement ${typeof t === 'string' ? t : ((t as { name?: string })?.name ?? '?')}]`;
    }
    if (o instanceof Map) return `[Map(${o.size})]`;
    if (o instanceof Set) return `[Set(${o.size})]`;
    if (depth >= MAX_DEPTH) return Array.isArray(o) ? `[Array(${o.length})]` : '[Object]';
    seen.add(o);
    let out: unknown;
    if (Array.isArray(o)) {
      const items = o.slice(0, MAX_ITEMS).map((x) => serializeValue(x, depth + 1, seen));
      if (o.length > MAX_ITEMS) items.push(`[+${o.length - MAX_ITEMS} more]`);
      out = items;
    } else {
      const res: Record<string, unknown> = {};
      const keys = Object.keys(o);
      for (const k of keys.slice(0, MAX_KEYS)) {
        let val: unknown;
        try {
          val = (o as Record<string, unknown>)[k];
        } catch {
          val = '[getter threw]';
        }
        res[k] = serializeValue(val, depth + 1, seen);
      }
      if (keys.length > MAX_KEYS) res['…'] = `+${keys.length - MAX_KEYS} more keys`;
      out = res;
    }
    seen.delete(o); // only true cycles count, not shared references
    return out;
  } catch {
    return '[unserialisable]';
  }
}

/** Serialise props, or null when there is nothing useful. Never exceeds `maxBytes` of JSON. */
export function safeSerializeProps(props: unknown, maxBytes = PROPS_MAX_BYTES): Record<string, unknown> | null {
  if (props == null || typeof props !== 'object') return null;
  const out = serializeValue(props, 0, new WeakSet());
  if (!out || typeof out !== 'object' || Array.isArray(out)) return null;
  const rec = out as Record<string, unknown>;
  if (JSON.stringify(rec).length <= maxBytes) return rec;
  // Too big: keep whole keys, in order, while they fit.
  const kept: Record<string, unknown> = {};
  let size = 2;
  for (const [k, v] of Object.entries(rec)) {
    const piece = JSON.stringify(v).length + k.length + 4;
    if (size + piece > maxBytes - 40) {
      kept['…'] = 'truncated (4 KB cap)';
      break;
    }
    kept[k] = v;
    size += piece;
  }
  return kept;
}
