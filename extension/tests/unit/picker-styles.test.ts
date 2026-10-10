import { describe, expect, it } from 'vitest';
import { CURATED_STYLE_PROPS, curateStyles } from '../../src/lib/picker/styles';

const getter = (m: Record<string, string>) => (p: string) => m[p] ?? '';

describe('curateStyles', () => {
  it('keeps the curated properties and drops empty/noise values', () => {
    const out = curateStyles(
      getter({ display: 'block', color: 'rgb(0, 0, 0)', 'box-shadow': 'none', 'z-index': 'auto', margin: '0px', 'letter-spacing': 'normal' }),
    );
    expect(out).toEqual({ display: 'block', color: 'rgb(0, 0, 0)', margin: '0px' });
  });

  it('includes flex properties only for flex containers', () => {
    expect(curateStyles(getter({ display: 'block', 'flex-direction': 'row' }))).not.toHaveProperty('flex-direction');
    const flex = curateStyles(getter({ display: 'flex', 'flex-direction': 'column', 'justify-content': 'center' }));
    expect(flex).toMatchObject({ 'flex-direction': 'column', 'justify-content': 'center' });
  });

  it('includes grid properties only for grid containers', () => {
    const grid = curateStyles(getter({ display: 'grid', 'grid-template-columns': '1fr 1fr', gap: '8px' }));
    expect(grid).toMatchObject({ 'grid-template-columns': '1fr 1fr', gap: '8px' });
    expect(curateStyles(getter({ display: 'block', 'grid-template-columns': '1fr' }))).not.toHaveProperty('grid-template-columns');
  });

  it('covers every property SPEC §5.3 lists', () => {
    for (const p of [
      'display', 'position', 'gap', 'margin', 'padding', 'width', 'height', 'font-family', 'font-size', 'font-weight',
      'line-height', 'letter-spacing', 'color', 'background-color', 'border', 'border-radius', 'box-shadow', 'opacity',
      'z-index', 'flex-direction', 'grid-template-columns',
    ]) {
      expect(CURATED_STYLE_PROPS).toContain(p);
    }
  });
});
