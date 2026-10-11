<script lang="ts">
  import Loader2Icon from '@lucide/svelte/icons/loader-circle';
  import XIcon from '@lucide/svelte/icons/x';
  import { Badge } from '$lib/components/ui/badge';
  import { Button } from '$lib/components/ui/button';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { cn } from '$lib/utils';
  import type { PickChip } from '../picker-protocol';
  import { chipTitle } from '../picks/chip';

  let {
    picks,
    onhover,
    oninsert,
    onremove,
    onclear,
  }: {
    picks: PickChip[];
    onhover: (id: number | null) => void;
    oninsert: (id: number) => void;
    onremove: (id: number) => void;
    onclear: () => void;
  } = $props();

  function tooltip(chip: PickChip): string {
    const parts = [chip.label, chip.tooltip];
    if (chip.stale) parts.push('Stale: the element is no longer on the page');
    if (chip.dimmed) parts.push('Already sent in an earlier message; still usable as [' + chip.id + ']');
    return parts.join('\n');
  }
</script>

{#if picks.length > 0}
  <div class="tray flex flex-none items-start gap-2 border-t bg-card px-3 py-2" role="group" aria-label="Picks">
    <div class="chips flex min-w-0 flex-1 flex-wrap gap-1.5">
      {#each picks as chip (chip.id)}
        <Tooltip.Root>
          <Tooltip.Trigger>
            {#snippet child({ props })}
              <div
                {...props}
                class={cn(
                  'chip inline-flex max-w-full items-center overflow-hidden rounded-4xl border bg-muted/60 text-xs',
                  chip.dimmed && 'dimmed opacity-55',
                  chip.stale && 'stale border-amber-500/50 bg-amber-500/10',
                )}
                data-pick-id={chip.id}
                data-status={chip.status}
                data-dimmed={chip.dimmed}
                data-stale={chip.stale}
                data-tip={tooltip(chip)}
                role="presentation"
                onmouseenter={() => onhover(chip.id)}
                onmouseleave={() => onhover(null)}
              >
                <button
                  class="main inline-flex min-w-0 cursor-pointer items-center gap-1.5 py-1 pr-1 pl-2.5 outline-none focus-visible:underline"
                  type="button"
                  onclick={() => oninsert(chip.id)}
                  aria-label={`Insert [${chip.id}]`}
                >
                  <span class="num font-mono font-semibold text-primary">[{chip.id}]</span>
                  <span class="label max-w-[14em] truncate">{chipTitle(chip)}</span>
                  {#if chip.status === 'capturing' || chip.status === 'uploading'}
                    <Loader2Icon class="spinner size-3 animate-spin text-muted-foreground" role="status" aria-label="Capturing screenshot" />
                  {/if}
                  {#if chip.stale}<Badge variant="destructive" class="h-4 px-1.5 text-[10px] uppercase">stale</Badge>{/if}
                </button>
                <button
                  class="x flex cursor-pointer items-center py-1 pr-2 pl-1 text-muted-foreground outline-none hover:text-foreground"
                  type="button"
                  onclick={() => onremove(chip.id)}
                  aria-label={`Remove [${chip.id}]`}
                >
                  <XIcon class="size-3" />
                </button>
              </div>
            {/snippet}
          </Tooltip.Trigger>
          <Tooltip.Content class="whitespace-pre-line">{tooltip(chip)}</Tooltip.Content>
        </Tooltip.Root>
      {/each}
    </div>
    <Button variant="ghost" size="xs" class="text-muted-foreground" onclick={onclear}>Clear all</Button>
  </div>
{/if}
