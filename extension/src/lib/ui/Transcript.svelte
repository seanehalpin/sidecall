<script lang="ts">
  import MessageSquareIcon from '@lucide/svelte/icons/message-square';
  import * as Conversation from '$lib/components/ai-elements/conversation';
  import * as Message from '$lib/components/ai-elements/message';
  import { Shimmer } from '$lib/components/ai-elements/shimmer';
  import { badgeVariants } from '$lib/components/ui/badge';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { cn } from '$lib/utils';
  import type { PickChip } from '../picker-protocol';
  import { segmentMarkers } from '../picks/markers';
  import type { Task } from '../protocol';
  import type { PickHistoryEntry, TaskStep, TranscriptItem } from '../rail-state';
  import TaskCard from './TaskCard.svelte';

  let {
    entries,
    partial,
    tasks = [],
    taskSteps = {},
    picks = [],
    pickHistory = {},
    thinking = false,
    onhighlight = () => {},
  }: {
    entries: TranscriptItem[];
    partial: string | null;
    tasks?: Task[];
    taskSteps?: Record<string, TaskStep[]>;
    picks?: PickChip[];
    pickHistory?: Record<number, PickHistoryEntry>;
    /** A voice turn is being worked on and no reply text has started streaming yet. */
    thinking?: boolean;
    onhighlight?: (id: number | null) => void;
  } = $props();

  const liveIds = $derived(new Set(picks.map((p) => p.id)));
  const taskById = $derived(new Map(tasks.map((t) => [t.id, t])));
  const streamingAgent = $derived(entries.some((e) => e.role === 'agent' && e.streaming));
  const showThinking = $derived(thinking && !streamingAgent);

  /** The "Task queued" system row is where a task lives in the conversation. */
  function queuedTask(entry: TranscriptItem): Task | undefined {
    if (!entry.taskId || !entry.text.startsWith('Task queued')) return undefined;
    return taskById.get(entry.taskId);
  }
</script>

<Conversation.Root class="min-h-0 flex-1" aria-live="polite">
  <Conversation.Content>
    {#if entries.length === 0 && !partial}
      <Conversation.EmptyState
        class="empty"
        title="Talk to the agent about your app"
        description="Type below, or start a voice call. ⌥-click elements on the page to point at them."
      >
        {#snippet icon()}<MessageSquareIcon class="size-6" />{/snippet}
      </Conversation.EmptyState>
    {/if}
    {#each entries as entry (entry.role + ':' + entry.turnId)}
      {#if entry.role === 'system'}
        {@const task = queuedTask(entry)}
        <div
          class={cn('entry system', !task && 'text-center text-xs text-muted-foreground')}
          data-role="system"
          data-turn-id={entry.turnId}
          data-streaming={entry.streaming}
        >
          {#if task}
            <TaskCard {task} title={entry.text} steps={taskSteps[task.id] ?? []} />
          {:else}
            {entry.text}
          {/if}
        </div>
      {:else if entry.role === 'user'}
        <Message.Root from="user" class="entry user" data-role="user" data-turn-id={entry.turnId} data-streaming={entry.streaming}>
          <Message.Content class="whitespace-pre-wrap">
            <p>
              {#each segmentMarkers(entry.text) as seg, i (i)}{#if seg.kind === 'marker' && pickHistory[seg.id]}{@const known = pickHistory[seg.id]}{@const live = liveIds.has(seg.id)}{@const tip = `${known?.label ?? ''}\n${known?.tooltip ?? ''}`}<Tooltip.Root
                    ><Tooltip.Trigger
                      >{#snippet child({ props })}<span
                          {...props}
                          class={cn(badgeVariants({ variant: 'outline' }), 'marker mx-0.5 font-mono', live ? 'live cursor-pointer' : 'cursor-default')}
                          data-marker={seg.id}
                          data-tip={tip}
                          role="presentation"
                          onmouseenter={() => live && onhighlight(seg.id)}
                          onmouseleave={() => live && onhighlight(null)}>{seg.raw}</span
                        >{/snippet}</Tooltip.Trigger
                    ><Tooltip.Content class="whitespace-pre-line">{tip}</Tooltip.Content></Tooltip.Root
                  >{:else if seg.kind === 'marker'}{seg.raw}{:else}{seg.text}{/if}{/each}
            </p>
          </Message.Content>
        </Message.Root>
      {:else}
        <Message.Root from="assistant" class="entry agent" data-role="agent" data-turn-id={entry.turnId} data-streaming={entry.streaming}>
          <Message.Content>
            {#if entry.streaming && !entry.text}
              <Shimmer>Thinking…</Shimmer>
            {:else}
              <Message.Response content={entry.text} />
            {/if}
          </Message.Content>
        </Message.Root>
      {/if}
    {/each}
    {#if partial}
      <Message.Root from="user" class="entry user partial" data-role="user">
        <Message.Content class="opacity-60">{partial}</Message.Content>
      </Message.Root>
    {/if}
    {#if showThinking}
      <Message.Root from="assistant" class="thinking" role="status">
        <Message.Content><Shimmer>Thinking…</Shimmer></Message.Content>
      </Message.Root>
    {/if}
  </Conversation.Content>
  <Conversation.ScrollButton />
</Conversation.Root>
