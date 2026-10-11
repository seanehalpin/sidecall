<script lang="ts">
  import BanIcon from '@lucide/svelte/icons/ban';
  import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
  import CircleXIcon from '@lucide/svelte/icons/circle-x';
  import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
  import LoaderIcon from '@lucide/svelte/icons/loader-circle';
  import {
    Queue,
    QueueItem,
    QueueItemContent,
    QueueItemDescription,
    QueueList,
    QueueSection,
    QueueSectionContent,
    QueueSectionLabel,
  } from '$lib/components/ai-elements/queue';
  import { Badge } from '$lib/components/ui/badge';
  import { Button } from '$lib/components/ui/button';
  import { CollapsibleTrigger } from '$lib/components/ui/collapsible';
  import { cn } from '$lib/utils';
  import type { Task } from '../protocol';
  import { groupTasks, reorderIds } from '../rail-state';

  let {
    tasks,
    lastTaskEvent,
    disabled,
    oncancel,
    onreorder,
  }: {
    tasks: Task[];
    lastTaskEvent: Record<string, string>;
    disabled: boolean;
    oncancel: (id: string) => void;
    onreorder: (ids: string[]) => void;
  } = $props();

  const FINISHED_SHOWN = 5;

  let open = $state(true);
  let dragId: string | null = $state(null);
  let overId: string | null = $state(null);

  const groups = $derived(groupTasks(tasks));
  const active = $derived(groups.running.length + groups.queued.length);

  function statusLine(task: Task): string {
    if (task.status === 'blocked') return 'Waiting for your approval';
    return lastTaskEvent[task.id] ?? task.lastEvent ?? 'Starting…';
  }

  function ondragstart(e: DragEvent, id: string) {
    dragId = id;
    e.dataTransfer?.setData('text/plain', id);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }

  function ondragover(e: DragEvent, id: string | null) {
    if (dragId === null) return;
    e.preventDefault();
    overId = id;
  }

  function ondrop(e: DragEvent, beforeId: string | null) {
    e.preventDefault();
    const id = dragId ?? e.dataTransfer?.getData('text/plain') ?? null;
    dragId = null;
    overId = null;
    if (!id) return;
    const ids = groups.queued.map((t) => t.id);
    const next = reorderIds(ids, id, beforeId);
    if (next.join() !== ids.join()) onreorder(next);
  }

  function ondragend() {
    dragId = null;
    overId = null;
  }
</script>

{#if tasks.length > 0}
  <Queue class="queue flex-none rounded-none border-x-0 border-b-0 bg-card px-3 py-1.5 shadow-none" role="region" aria-label="Task queue">
    <QueueSection bind:open>
      <CollapsibleTrigger class="group flex w-full cursor-pointer items-center py-1 text-sm font-medium outline-none">
        <QueueSectionLabel label="Tasks">
          {#if active > 0}<Badge variant="secondary" class="count h-4 px-1.5 text-[10px]">{active}</Badge>{/if}
        </QueueSectionLabel>
      </CollapsibleTrigger>
      <QueueSectionContent>
        <QueueList class="mt-1">
          {#each groups.running as task (task.id)}
            <QueueItem class="task running border border-primary/40 bg-primary/5" data-task-id={task.id} data-status={task.status}>
              <div class="flex items-center gap-2">
                <LoaderIcon class="size-3.5 shrink-0 animate-spin text-primary" aria-hidden="true" />
                <QueueItemContent class="title font-medium text-foreground">{task.title}</QueueItemContent>
                <Button variant="outline" size="xs" {disabled} onclick={() => oncancel(task.id)}>Stop</Button>
              </div>
              <QueueItemDescription class="status ml-5.5 truncate">{statusLine(task)}</QueueItemDescription>
            </QueueItem>
          {/each}
          {#each groups.queued as task (task.id)}
            <QueueItem
              class={cn('task queued cursor-grab', dragId === task.id && 'dragging opacity-40', overId === task.id && dragId !== task.id && 'over shadow-[inset_0_2px_0_var(--color-primary)]')}
              data-task-id={task.id}
              data-status={task.status}
              draggable={!disabled}
              ondragstart={(e) => ondragstart(e, task.id)}
              ondragover={(e) => ondragover(e, task.id)}
              ondrop={(e) => ondrop(e, task.id)}
              {ondragend}
            >
              <div class="flex items-center gap-2">
                <GripVerticalIcon class="grip size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                <QueueItemContent class="title">{task.title}</QueueItemContent>
                <Button variant="ghost" size="xs" {disabled} onclick={() => oncancel(task.id)}>Cancel</Button>
              </div>
              <QueueItemDescription class="status ml-5.5">Queued</QueueItemDescription>
            </QueueItem>
          {/each}
          {#if groups.queued.length > 1 && dragId !== null}
            <li
              class={cn('drop-end h-2.5 rounded', overId === null && 'over shadow-[inset_0_2px_0_var(--color-primary)]')}
              ondragover={(e) => ondragover(e, null)}
              ondrop={(e) => ondrop(e, null)}
              aria-hidden="true"
            ></li>
          {/if}
          {#each groups.finished.slice(0, FINISHED_SHOWN) as task (task.id)}
            <QueueItem class={cn('task finished opacity-60', task.status)} data-task-id={task.id} data-status={task.status}>
              <div class="flex items-center gap-2">
                {#if task.status === 'completed'}
                  <CircleCheckIcon class="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                {:else if task.status === 'failed'}
                  <CircleXIcon class="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
                {:else}
                  <BanIcon class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                {/if}
                <QueueItemContent class="title">{task.title}</QueueItemContent>
              </div>
              <QueueItemDescription class="status ml-5.5 line-clamp-2">
                {#if task.status === 'completed'}
                  {task.summary ?? 'Done'}
                {:else if task.status === 'failed'}
                  <span class="error-text text-destructive">Failed: {task.error ?? 'unknown error'}</span>
                {:else}
                  Cancelled
                {/if}
              </QueueItemDescription>
            </QueueItem>
          {/each}
        </QueueList>
      </QueueSectionContent>
    </QueueSection>
  </Queue>
{/if}
