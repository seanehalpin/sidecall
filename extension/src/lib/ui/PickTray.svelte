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
  <div class="tray flex w-full items-start gap-2" role="group" aria-label="Picks">
    <div class="chips flex min-w-0 flex-1 flex-wrap gap-1">
      {#each picks as chip (chip.id)}
        <Tooltip.Root>
          <Tooltip.Trigger>
            {#snippet child({ props })}
              <Badge
                {...props}
                variant={chip.stale ? 'destructive' : 'secondary'}
                class={cn('chip h-6 max-w-full gap-0 p-0', chip.dimmed && 'dimmed opacity-50', chip.stale && 'stale')}
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
                  class="main inline-flex h-full min-w-0 cursor-pointer items-center gap-1 pr-1 pl-2 outline-none focus-visible:underline"
                  type="button"
                  onclick={() => oninsert(chip.id)}
                  aria-label={`Insert [${chip.id}]`}
                >
                  <span class="num font-mono">[{chip.id}]</span>
                  <span class="label max-w-[14em] truncate font-normal">{chipTitle(chip)}</span>
                  {#if chip.status === 'capturing' || chip.status === 'uploading'}
                    <Loader2Icon class="spinner size-3 animate-spin" role="status" aria-label="Capturing screenshot" />
                  {/if}
                  {#if chip.stale}<span class="font-normal">· stale</span>{/if}
                </button>
                <button
                  class="x flex h-full cursor-pointer items-center pr-2 pl-0.5 opacity-60 outline-none hover:opacity-100"
                  type="button"
                  onclick={() => onremove(chip.id)}
                  aria-label={`Remove [${chip.id}]`}
                >
                  <XIcon class="size-3" />
                </button>
              </Badge>
            {/snippet}
          </Tooltip.Trigger>
          <Tooltip.Content class="whitespace-pre-line">{tooltip(chip)}</Tooltip.Content>
        </Tooltip.Root>
      {/each}
    </div>
    <Button variant="ghost" size="xs" class="text-muted-foreground" onclick={onclear}>Clear all</Button>
  </div>
{/if}
