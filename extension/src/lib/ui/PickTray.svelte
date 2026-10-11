<script lang="ts">
  // The elements picked for the next message. They ride along with it when it's sent
  // (they don't get typed into the text); hover highlights the pin on the page.
  import Loader2Icon from '@lucide/svelte/icons/loader-circle';
  import XIcon from '@lucide/svelte/icons/x';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import type { PickChip } from '../picker-protocol';
  import PickChipView from './PickChipView.svelte';

  let {
    picks,
    onhover,
    onremove,
    onclear,
  }: {
    picks: PickChip[];
    onhover: (id: number | null) => void;
    onremove: (id: number) => void;
    onclear: () => void;
  } = $props();

  function tooltip(chip: PickChip): string {
    const parts = [chip.path, chip.tooltip];
    if (chip.stale) parts.push('Stale: the element is no longer on the page');
    if (chip.dimmed) parts.push(`Sent with an earlier message; still usable as [${chip.id}]`);
    return parts.join('\n');
  }
</script>

{#if picks.length > 0}
  <div class="tray flex w-full items-start gap-2 px-1 pt-1 py-2 border-b" role="group" aria-label="Picks">
    <div class="chips flex min-w-0 flex-1 flex-wrap gap-1.5">
      {#each picks as chip (chip.id)}
        <Tooltip.Root>
          <Tooltip.Trigger>
            {#snippet child({ props })}
              <PickChipView
                {...props}
                class="chip"
                num={chip.id}
                label={chip.path}
                stale={chip.stale}
                dimmed={chip.dimmed}
                data-pick-id={chip.id}
                data-status={chip.status}
                data-dimmed={chip.dimmed}
                data-stale={chip.stale}
                data-tip={tooltip(chip)}
                onmouseenter={() => onhover(chip.id)}
                onmouseleave={() => onhover(null)}
              >
                {#snippet trailing()}
                  {#if chip.status === 'capturing' || chip.status === 'uploading'}
                    <Loader2Icon class="spinner size-3 shrink-0 animate-spin text-muted-foreground" role="status" aria-label="Capturing screenshot" />
                  {/if}
                  <button
                    class="x inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground outline-none hover:bg-[var(--studio-inspectFill)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                    type="button"
                    onclick={() => onremove(chip.id)}
                    aria-label={`Remove [${chip.id}]`}
                  >
                    <XIcon class="size-3" />
                  </button>
                {/snippet}
              </PickChipView>
            {/snippet}
          </Tooltip.Trigger>
          <Tooltip.Content class="whitespace-pre-line">{tooltip(chip)}</Tooltip.Content>
        </Tooltip.Root>
      {/each}
    </div>
    {#if picks.length > 0}
      <button class="clear h-7 shrink-0 cursor-pointer px-1.5 text-xs text-muted-foreground hover:text-foreground" type="button" onclick={onclear}
        >Clear all</button
      >
    {/if}
  </div>
{/if}
