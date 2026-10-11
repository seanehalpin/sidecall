<script lang="ts">
  import MessageSquareIcon from '@lucide/svelte/icons/message-square';
  import * as Conversation from '$lib/components/ai-elements/conversation';
  import PickChipView from './PickChipView.svelte';
  import * as Message from '$lib/components/ai-elements/message';
  import { Shimmer } from '$lib/components/ai-elements/shimmer';
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
  <!-- Content is the scroller (the stick-to-bottom context watches it), so it needs a height and overflow. -->
  <Conversation.Content class="min-h-0 flex-1 overflow-y-auto [&>*]:shrink-0">
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
                          class={cn(
                            'marker mx-0.5 inline-flex size-5 items-center justify-center rounded-full align-middle text-[11px] font-medium tabular-nums',
                            'bg-[var(--studio-inspect)] text-[var(--studio-onInspect)]',
                            live ? 'live cursor-pointer' : 'cursor-default opacity-70',
                          )}
                          data-marker={seg.id}
                          data-tip={tip}
                          role="presentation"
                          onmouseenter={() => live && onhighlight(seg.id)}
                          onmouseleave={() => live && onhighlight(null)}>{seg.id}</span
                        >{/snippet}</Tooltip.Trigger
                    ><Tooltip.Content class="whitespace-pre-line">{tip}</Tooltip.Content></Tooltip.Root
                  >{:else if seg.kind === 'marker'}{seg.raw}{:else}{seg.text}{/if}{/each}
            </p>
            {#if entry.picks && entry.picks.length > 0}
              <!-- The elements this message was sent with (attached, not typed). -->
              <div class="attached mt-1.5 flex flex-wrap gap-1">
                {#each entry.picks as pick (pick.id)}
                  {@const live = liveIds.has(pick.id)}
                  <PickChipView
                    class={cn('pick', live && 'cursor-pointer')}
                    size="sm"
                    num={pick.id}
                    label={pick.path}
                    data-pick-id={pick.id}
                    title={`${pick.path}\n${pick.tooltip}`}
                    onmouseenter={() => live && onhighlight(pick.id)}
                    onmouseleave={() => live && onhighlight(null)}
                  />
                {/each}
              </div>
            {/if}
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
