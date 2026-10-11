<script lang="ts">
  // Tasks Claude Code is working on (or will next), stacked above the message box as they're
  // added. A finished task's card goes away; the chat gets a one-line note instead.
  import { fly } from 'svelte/transition';
  import XIcon from '@lucide/svelte/icons/x';
  import type { Task } from '../protocol';
  import ClaudeMark from './ClaudeMark.svelte';

  let {
    tasks,
    lastTaskEvent,
    disabled,
    oncancel,
  }: {
    tasks: Task[];
    lastTaskEvent: Record<string, string>;
    disabled: boolean;
    oncancel: (id: string) => void;
  } = $props();

  const active = $derived(tasks.filter((t) => t.status === 'running' || t.status === 'blocked' || t.status === 'queued'));

  function statusLine(t: Task): string {
    if (t.status === 'blocked') return 'Waiting for your approval';
    if (t.status === 'queued') return 'Queued';
    return lastTaskEvent[t.id] ?? t.lastEvent ?? 'Starting…';
  }
</script>

{#if active.length > 0}
  <div class="task-stack queue flex flex-col gap-1.5" role="region" aria-label="Task queue">
    {#each active as t (t.id)}
      <div
        class="sc-taskcard task flex items-center gap-2.5 rounded-lg border px-3 py-2"
        class:queued={t.status === 'queued'}
        data-task-id={t.id}
        data-status={t.status}
        in:fly={{ y: 8, duration: 200 }}
        out:fly={{ y: -4, duration: 150 }}
      >
        <ClaudeMark size={18} active={t.status === 'running'} class={t.status === 'queued' ? 'opacity-40' : ''} />
        <div class="min-w-0 flex-1">
          <div class="title truncate text-sm">{t.title}</div>
          <div class="status truncate text-xs text-muted-foreground">{statusLine(t)}</div>
        </div>
        <button
          class="inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
          type="button"
          {disabled}
          aria-label={t.status === 'queued' ? 'Cancel' : 'Stop'}
          title={t.status === 'queued' ? 'Cancel this task' : 'Stop this task'}
          onclick={() => oncancel(t.id)}
        >
          <XIcon class="size-3.5" />
        </button>
      </div>
    {/each}
  </div>
{/if}
