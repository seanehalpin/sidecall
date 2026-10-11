<script lang="ts">
  import * as TaskUI from '$lib/components/ai-elements/task';
  import { Shimmer } from '$lib/components/ai-elements/shimmer';
  import type { Task } from '../protocol';
  import type { TaskStep } from '../rail-state';

  let { task, title, steps }: { task: Task; title: string; steps: TaskStep[] } = $props();

  const active = $derived(task.status === 'running' || task.status === 'blocked' || task.status === 'queued');

  /** "editing src/a.svelte" -> verb "editing", file "src/a.svelte"; null when it doesn't look like a path. */
  function fileStep(step: TaskStep): { verb: string; file: string } | null {
    if (step.kind !== 'edit') return null;
    const m = /^(\S+)\s+(\S+)$/.exec(step.text);
    return m && m[1] && m[2] ? { verb: m[1], file: m[2] } : null;
  }
</script>

<TaskUI.Root class="task-card" data-status={task.status} open={false}>
  <TaskUI.Trigger {title} />
  <TaskUI.Content>
    {#each steps as step, i (i)}
      {@const file = fileStep(step)}
      <TaskUI.Item class="step" data-kind={step.kind}>
        {#if file}
          <span class="inline-flex items-center gap-1">{file.verb} <TaskUI.ItemFile>{file.file}</TaskUI.ItemFile></span>
        {:else if active && i === steps.length - 1}
          <Shimmer as="span">{step.text}</Shimmer>
        {:else}
          {step.text}
        {/if}
      </TaskUI.Item>
    {/each}
    {#if task.status === 'queued'}
      <TaskUI.Item>Queued</TaskUI.Item>
    {:else if task.status === 'blocked'}
      <TaskUI.Item>Waiting for your approval</TaskUI.Item>
    {:else if task.status === 'running' && steps.length === 0}
      <TaskUI.Item><Shimmer as="span">Starting…</Shimmer></TaskUI.Item>
    {:else if task.status === 'completed'}
      <TaskUI.Item class="text-foreground">{task.summary ?? 'Done'}</TaskUI.Item>
      {#if task.files.length > 0}
        <TaskUI.Item class="flex flex-wrap gap-1">
          {#each task.files as f (f)}<TaskUI.ItemFile>{f}</TaskUI.ItemFile>{/each}
        </TaskUI.Item>
      {/if}
    {:else if task.status === 'failed'}
      <TaskUI.Item class="text-destructive">Failed: {task.error ?? 'unknown error'}</TaskUI.Item>
    {:else if task.status === 'cancelled'}
      <TaskUI.Item>Cancelled</TaskUI.Item>
    {/if}
  </TaskUI.Content>
</TaskUI.Root>
