<script lang="ts">
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
  <div class="tray" role="group" aria-label="Picks">
    <div class="chips">
      {#each picks as chip (chip.id)}
        <div
          class="chip"
          class:dimmed={chip.dimmed}
          class:stale={chip.stale}
          data-pick-id={chip.id}
          data-status={chip.status}
          data-dimmed={chip.dimmed}
          data-stale={chip.stale}
          title={tooltip(chip)}
          role="presentation"
          onmouseenter={() => onhover(chip.id)}
          onmouseleave={() => onhover(null)}
        >
          <button class="main" type="button" onclick={() => oninsert(chip.id)} aria-label={`Insert [${chip.id}]`}>
            <span class="num">[{chip.id}]</span>
            <span class="label">{chipTitle(chip)}</span>
            {#if chip.status === 'capturing' || chip.status === 'uploading'}
              <span class="spinner" role="status" aria-label="Capturing screenshot"></span>
            {/if}
            {#if chip.stale}<span class="badge">stale</span>{/if}
          </button>
          <button class="x" type="button" onclick={() => onremove(chip.id)} aria-label={`Remove [${chip.id}]`}>×</button>
        </div>
      {/each}
    </div>
    <button class="clear" type="button" onclick={onclear}>Clear all</button>
  </div>
{/if}

<style>
  .tray {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 12px;
    border-top: 1px solid var(--border);
    flex: none;
  }
  .chips {
    flex: 1;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    min-width: 0;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    max-width: 100%;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--bg-subtle);
    overflow: hidden;
  }
  .chip.dimmed {
    opacity: 0.55;
  }
  .chip.stale {
    border-color: var(--warn-border);
    background: var(--warn-bg);
  }
  .chip button {
    border: none;
    background: transparent;
    padding: 2px 6px;
    font: inherit;
    color: inherit;
    cursor: pointer;
  }
  .main {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    padding-left: 8px !important;
  }
  .num {
    font-family: var(--mono);
    font-weight: 600;
    color: var(--accent);
  }
  .label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 14em;
  }
  .x {
    color: var(--fg-muted) !important;
    padding-right: 8px !important;
  }
  .badge {
    font-size: 10px;
    text-transform: uppercase;
    color: var(--danger);
  }
  .spinner {
    width: 10px;
    height: 10px;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .clear {
    border: none;
    background: transparent;
    color: var(--fg-muted);
    font-size: 11px;
    padding: 2px 4px;
    white-space: nowrap;
  }
</style>
