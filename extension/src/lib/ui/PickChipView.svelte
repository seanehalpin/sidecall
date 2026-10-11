<script lang="ts">
  // One picked element, drawn in the picker's own colours (--studio-inspect…, from
  // src/styles/colors.css) so the chip reads as the same thing as the outline on the page.
  import type { Snippet } from 'svelte';
  import type { HTMLAttributes } from 'svelte/elements';
  import { cn } from '$lib/utils';

  let {
    num,
    label,
    stale = false,
    dimmed = false,
    size = 'md',
    class: className,
    trailing,
    ...rest
  }: HTMLAttributes<HTMLSpanElement> & {
    num: number;
    label: string;
    stale?: boolean;
    dimmed?: boolean;
    size?: 'sm' | 'md';
    trailing?: Snippet;
  } = $props();
</script>

<span
  class={cn(
    'inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-full border align-middle',
    'border-[color-mix(in_srgb,var(--studio-inspect)_35%,transparent)] bg-[var(--studio-inspectFill)] text-foreground',
    size === 'md' ? 'h-7 py-0.5 pr-1.5 pl-1.5 text-xs' : 'h-5 py-0.5 pr-2 pl-2 text-[11px]',
    stale && 'border-dashed border-[var(--studio-inspectStale)] bg-transparent text-muted-foreground',
    dimmed && 'opacity-60',
    className,
  )}
  {...rest}
>
  <span
    class={cn(
      'num inline-flex shrink-0 items-center justify-center rounded-full font-medium tabular-nums',
      'bg-[var(--studio-inspect)] text-[var(--studio-onInspect)]',
      size === 'md' ? 'size-5 text-xs' : 'size-4 text-[10px]',
      stale && 'bg-[var(--studio-inspectStale)]',
    )}>{num}</span
  >
  <span class="label min-w-0 truncate">{label}</span>
  {@render trailing?.()}
</span>
