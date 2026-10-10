// [n] pick markers in message text (SPEC §6.9 "Typed turns").

const MARKER = /\[(\d{1,6})\]/g;

/** Distinct pick ids referenced as [n] in `text`, in order of first appearance. */
export function parseMarkers(text: string): number[] {
  const seen = new Set<number>();
  for (const m of text.matchAll(MARKER)) {
    const id = Number(m[1]);
    if (id > 0) seen.add(id);
  }
  return [...seen];
}

export type MarkerSegment = { kind: 'text'; text: string } | { kind: 'marker'; id: number; raw: string };

/** Split text into plain runs and [n] markers, for rendering inline chips. */
export function segmentMarkers(text: string): MarkerSegment[] {
  const out: MarkerSegment[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER)) {
    const id = Number(m[1]);
    if (id <= 0) continue;
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: 'text', text: text.slice(last, at) });
    out.push({ kind: 'marker', id, raw: m[0] });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}
