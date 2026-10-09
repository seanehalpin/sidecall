<script lang="ts">
  import type { TranscriptItem } from '../rail-state';

  let { entries, partial }: { entries: TranscriptItem[]; partial: string | null } = $props();

  let scroller: HTMLElement | undefined = $state();
  // Follow new content unless the user has scrolled up to read.
  let follow = true;

  function onscroll() {
    if (!scroller) return;
    follow = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 32;
  }

  $effect(() => {
    // Re-run when entries change, including streaming text growing.
    void entries.length;
    void entries.at(-1)?.text;
    void partial;
    if (follow && scroller) scroller.scrollTop = scroller.scrollHeight;
  });
</script>

<div class="transcript" bind:this={scroller} {onscroll} role="log" aria-live="polite">
  {#if entries.length === 0 && !partial}
    <p class="empty muted">Type below to talk to the agent about your app.</p>
  {/if}
  {#each entries as entry (entry.role + ':' + entry.turnId)}
    <div class="entry {entry.role}" data-role={entry.role} data-turn-id={entry.turnId} data-streaming={entry.streaming}>
      {#if entry.role === 'system'}
        <span>{entry.text}</span>
      {:else}
        <div class="bubble">{entry.text}{#if entry.streaming}<span class="caret" aria-hidden="true"></span>{/if}</div>
      {/if}
    </div>
  {/each}
  {#if partial}
    <div class="entry user partial"><div class="bubble">{partial}</div></div>
  {/if}
</div>

<style>
  .transcript {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .empty {
    margin: auto;
    text-align: center;
  }
  .entry {
    display: flex;
  }
  .bubble {
    max-width: 88%;
    padding: 6px 10px;
    border-radius: 12px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .user {
    justify-content: flex-end;
  }
  .user .bubble {
    background: var(--user-bubble);
    border-bottom-right-radius: 4px;
  }
  .agent .bubble {
    background: var(--bg-subtle);
    border-bottom-left-radius: 4px;
  }
  .partial .bubble {
    opacity: 0.6;
    font-style: italic;
  }
  .system {
    justify-content: center;
    font-size: 11px;
    color: var(--fg-muted);
    text-align: center;
  }
  .caret {
    display: inline-block;
    width: 6px;
    height: 1em;
    margin-left: 2px;
    vertical-align: text-bottom;
    background: currentColor;
    opacity: 0.4;
    animation: blink 1s steps(2) infinite;
  }
  @keyframes blink {
    to {
      opacity: 0;
    }
  }
</style>
