// Turning a PickDraft into what the rail shows (PickChip) and what the page draws (PinSpec).
import type { Anchor, PickChip, PickDraft, PinSpec } from '../picker-protocol';

/** First class token, for "div.card-header". */
function tagClass(draft: PickDraft): string {
  const el = draft.element;
  if (!el) return 'region';
  const first = (el.attributes['class'] ?? '').trim().split(/\s+/)[0];
  return first ? `${el.tag}.${first}` : el.tag;
}

function componentName(draft: PickDraft): string | null {
  return draft.framework.componentChain[0]?.name ?? null;
}

/** Source as shown next to a pick: "src/lib/Card.svelte:14" (line only; column in tooltips). */
function sourceText(draft: PickDraft, withColumn: boolean): string | null {
  const s = draft.framework.source;
  if (s) return `${s.file}:${s.line}${withColumn ? `:${s.column}` : ''}`;
  const c = draft.framework.componentChain.find((e) => e.file);
  if (c?.file) return c.line ? `${c.file}:${c.line}` : c.file;
  return null;
}

export function repeatText(repeat: PickDraft['repeat']): string | null {
  return repeat ? `repeated ${repeat.count}×, instance ${repeat.index + 1}` : null;
}

export function buildChip(id: number, draft: PickDraft): PickChip {
  const component = componentName(draft);
  const base = draft.kind === 'region' ? regionLabel(draft) : tagClass(draft);
  const source = draft.framework.source;
  const where = sourceText(draft, true);
  const tooltip = [where ? `${where}${source ? ` (${source.precision})` : ''}` : 'source unknown', repeatText(draft.repeat)]
    .filter(Boolean)
    .join(' · ');
  return {
    id,
    kind: draft.kind,
    label: component ? `${base} · ${component}` : base,
    tooltip,
    component,
    path: chipPath(draft, base),
    source: source ? { file: source.file, line: source.line, precision: source.precision } : null,
    repeat: draft.repeat,
    stale: false,
    dimmed: false,
    status: 'capturing',
  };
}

/**
 * "CardList › Card › h2.card-title": up to two enclosing components (outermost first,
 * skipping Svelte blocks like {#each}) and then the element itself.
 */
export function chipPath(draft: PickDraft, base: string): string {
  const names = draft.framework.componentChain
    .map((c) => c.name)
    .filter((n) => n && !n.startsWith('{'))
    .slice(0, 2)
    .reverse();
  return [...names, base].join(' › ');
}

function regionLabel(draft: PickDraft): string {
  const r = draft.region?.rect;
  return r ? `region ${Math.round(r.w)}×${Math.round(r.h)}` : 'region';
}

/** What a chip is called in the tray and transcript: the component when known. */
export function chipTitle(chip: Pick<PickChip, 'component' | 'label'>): string {
  return chip.component ?? chip.label;
}

/** "CardHeader  src/lib/Card.svelte:14", or "div.card-header" when nothing better is known. */
export function pinLabel(draft: PickDraft): string {
  const name = componentName(draft) ?? tagClass(draft);
  const where = sourceText(draft, false);
  return where ? `${name}  ${where}` : name;
}

export function buildPin(id: number, draft: PickDraft, anchor: Anchor, dimmed: boolean): PinSpec {
  return { id, anchor, label: pinLabel(draft), dimmed };
}
