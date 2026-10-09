<script lang="ts">
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
  <section class="queue" aria-label="Task queue">
    <button class="toggle" onclick={() => (open = !open)} aria-expanded={open}>
      <span class="chev" class:open>▸</span>
      Tasks
      {#if active > 0}<span class="count">{active}</span>{/if}
    </button>
    {#if open}
      <ul>
        {#each groups.running as task (task.id)}
          <li class="task running" data-task-id={task.id} data-status={task.status}>
            <div class="main">
              <div class="title">{task.title}</div>
              <div class="status muted">{statusLine(task)}</div>
            </div>
            <button {disabled} onclick={() => oncancel(task.id)} title="Stop this task">Stop</button>
          </li>
        {/each}
        {#each groups.queued as task (task.id)}
          <li
            class="task queued"
            class:dragging={dragId === task.id}
            class:over={overId === task.id && dragId !== task.id}
            data-task-id={task.id}
            data-status={task.status}
            draggable={!disabled}
            ondragstart={(e) => ondragstart(e, task.id)}
            ondragover={(e) => ondragover(e, task.id)}
            ondrop={(e) => ondrop(e, task.id)}
            {ondragend}
          >
            <span class="grip" aria-hidden="true">⋮⋮</span>
            <div class="main">
              <div class="title">{task.title}</div>
              <div class="status muted">Queued</div>
            </div>
            <button {disabled} onclick={() => oncancel(task.id)} title="Cancel this task">Cancel</button>
          </li>
        {/each}
        {#if groups.queued.length > 1 && dragId !== null}
          <li
            class="drop-end"
            class:over={overId === null}
            ondragover={(e) => ondragover(e, null)}
            ondrop={(e) => ondrop(e, null)}
            aria-hidden="true"
          ></li>
        {/if}
        {#each groups.finished.slice(0, FINISHED_SHOWN) as task (task.id)}
          <li class="task finished {task.status}" data-task-id={task.id} data-status={task.status}>
            <div class="main">
              <div class="title">{task.title}</div>
              <div class="status">
                {#if task.status === 'completed'}
                  {task.summary ?? 'Done'}
                {:else if task.status === 'failed'}
                  <span class="error-text">Failed: {task.error ?? 'unknown error'}</span>
                {:else}
                  Cancelled
                {/if}
              </div>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .queue {
    border-top: 1px solid var(--border);
    max-height: 40%;
    overflow-y: auto;
    flex: none;
  }
  .toggle {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 6px;
    border: none;
    border-radius: 0;
    background: transparent;
    padding: 8px 12px;
    font-weight: 600;
    text-align: left;
  }
  .chev {
    display: inline-block;
    transition: transform 0.1s;
    font-size: 10px;
  }
  .chev.open {
    transform: rotate(90deg);
  }
  .count {
    font-weight: 500;
    font-size: 11px;
    background: var(--bg-subtle);
    border-radius: 10px;
    padding: 0 6px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0 12px 8px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .task {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
  }
  .main {
    flex: 1;
    min-width: 0;
  }
  .title {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .status {
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  .running {
    border-color: var(--accent);
  }
  .running .status {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .queued {
    cursor: grab;
  }
  .grip {
    color: var(--fg-muted);
    font-size: 10px;
    letter-spacing: -2px;
  }
  .dragging {
    opacity: 0.4;
  }
  .over {
    box-shadow: inset 0 2px 0 var(--accent);
  }
  .drop-end {
    height: 10px;
  }
  .finished {
    opacity: 0.6;
    border-style: dashed;
  }
</style>
