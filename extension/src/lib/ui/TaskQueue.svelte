<script lang="ts">
  import SquareIcon from '@lucide/svelte/icons/square';
  import XIcon from '@lucide/svelte/icons/x';
  import {
    Queue,
    QueueItem,
    QueueItemAction,
    QueueItemActions,
    QueueItemContent,
    QueueItemDescription,
    QueueItemIndicator,
    QueueList,
    QueueSection,
    QueueSectionContent,
    QueueSectionLabel,
    QueueSectionTrigger,
  } from '$lib/components/ai-elements/queue';
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

  let dragId: string | null = $state(null);
  let overId: string | null = $state(null);

  const groups = $derived(groupTasks(tasks));
  const active = $derived(groups.running.length + groups.queued.length);
  const finished = $derived(groups.finished.slice(0, FINISHED_SHOWN));

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
  <Queue class="queue" role="region" aria-label="Task queue">
    {#if active > 0}
      <QueueSection>
        <QueueSectionTrigger>
          <QueueSectionLabel count={active} label={active === 1 ? 'Task' : 'Tasks'} />
        </QueueSectionTrigger>
        <QueueSectionContent>
          <QueueList>
            {#each groups.running as task (task.id)}
              <QueueItem class="task running" data-task-id={task.id} data-status={task.status}>
                <div class="flex items-center gap-2">
                  <QueueItemIndicator class="animate-pulse border-primary bg-primary/20" />
                  <QueueItemContent class="title">{task.title}</QueueItemContent>
                  <QueueItemActions>
                    <QueueItemAction {disabled} aria-label="Stop" title="Stop" onclick={() => oncancel(task.id)}><SquareIcon /></QueueItemAction>
                  </QueueItemActions>
                </div>
                <QueueItemDescription class="status truncate">{statusLine(task)}</QueueItemDescription>
              </QueueItem>
            {/each}
            {#each groups.queued as task (task.id)}
              <QueueItem
                class={cn('task queued cursor-grab', dragId === task.id && 'dragging opacity-40', overId === task.id && dragId !== task.id && 'over bg-muted')}
                data-task-id={task.id}
                data-status={task.status}
                draggable={!disabled}
                ondragstart={(e) => ondragstart(e, task.id)}
                ondragover={(e) => ondragover(e, task.id)}
                ondrop={(e) => ondrop(e, task.id)}
                {ondragend}
              >
                <div class="flex items-center gap-2">
                  <QueueItemIndicator />
                  <QueueItemContent class="title">{task.title}</QueueItemContent>
                  <QueueItemActions>
                    <QueueItemAction {disabled} aria-label="Cancel" title="Cancel" onclick={() => oncancel(task.id)}><XIcon /></QueueItemAction>
                  </QueueItemActions>
                </div>
                <QueueItemDescription class="status">Queued</QueueItemDescription>
              </QueueItem>
            {/each}
            {#if groups.queued.length > 1 && dragId !== null}
              <li
                class={cn('drop-end h-2 rounded-md', overId === null && 'over bg-muted')}
                ondragover={(e) => ondragover(e, null)}
                ondrop={(e) => ondrop(e, null)}
                aria-hidden="true"
              ></li>
            {/if}
          </QueueList>
        </QueueSectionContent>
      </QueueSection>
    {/if}
    {#if finished.length > 0}
      <QueueSection>
        <QueueSectionTrigger>
          <QueueSectionLabel count={finished.length} label="Done" />
        </QueueSectionTrigger>
        <QueueSectionContent>
          <QueueList>
            {#each finished as task (task.id)}
              <QueueItem class={cn('task finished', task.status)} data-task-id={task.id} data-status={task.status}>
                <div class="flex items-center gap-2">
                  <QueueItemIndicator completed class={cn(task.status === 'failed' && 'border-destructive bg-destructive/20')} />
                  <QueueItemContent class="title" completed={task.status === 'completed'}>{task.title}</QueueItemContent>
                </div>
                <QueueItemDescription class="status line-clamp-2" completed={task.status === 'completed'}>
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
    {/if}
  </Queue>
{/if}
