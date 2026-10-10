// The curated computed-style list from SPEC §5.3, as a pure function over a getter.

const ALWAYS = [
  'display', 'position', 'gap', 'margin', 'padding', 'width', 'height', 'font-family', 'font-size',
  'font-weight', 'line-height', 'letter-spacing', 'color', 'background-color', 'border',
  'border-radius', 'box-shadow', 'opacity', 'z-index',
] as const;
const FLEX = [
  'flex-direction', 'flex-wrap', 'flex-grow', 'flex-shrink', 'flex-basis', 'justify-content',
  'align-items', 'align-content',
] as const;
const GRID = [
  'grid-template-columns', 'grid-template-rows', 'grid-auto-flow', 'justify-items', 'align-items',
] as const;

export const CURATED_STYLE_PROPS: readonly string[] = [...new Set([...ALWAYS, ...FLEX, ...GRID])];

/** Values that carry no information; dropped to keep the payload small. */
const NOISE: Record<string, string[]> = {
  'box-shadow': ['none'],
  'letter-spacing': ['normal'],
  'z-index': ['auto'],
  gap: ['normal', 'normal normal'],
  border: [''],
};

/** Collect the curated properties; flex/grid ones only when the display mode applies. */
export function curateStyles(get: (prop: string) => string): Record<string, string> {
  const out: Record<string, string> = {};
  const display = get('display');
  const props: string[] = [...ALWAYS];
  if (display.includes('flex')) props.push(...FLEX);
  if (display.includes('grid')) props.push(...GRID);
  for (const p of props) {
    const v = (get(p) ?? '').trim();
    if (!v || NOISE[p]?.includes(v)) continue;
    out[p] = v;
  }
  return out;
}
