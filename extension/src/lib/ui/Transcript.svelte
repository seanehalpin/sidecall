<script lang="ts">
  import MessageSquareIcon from '@lucide/svelte/icons/message-square';
  import * as Conversation from '$lib/components/ai-elements/conversation';
  import PickChipView from './PickChipView.svelte';
  import ClaudeMark from './ClaudeMark.svelte';
  import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';

  /** "Task completed: Title. Long report…" → ["Task completed: Title.", "Long report…"]. */
  function splitSystem(text: string): [string, string] {
    const m = /^(.+?[.!?])\s+(\S[\s\S]*)$/.exec(text.trim());
    return m ? [m[1] ?? text, m[2] ?? ''] : [text, ''];
  }
  import * as Message from '$lib/components/ai-elements/message';
  import { Shimmer } from '$lib/components/ai-elements/shimmer';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { cn } from '$lib/utils';
  import type { PickChip } from '../picker-protocol';
  import { segmentMarkers } from '../picks/markers';
  import type { Task } from '../protocol';
  import type { PickHistoryEntry, TaskStep, TranscriptItem } from '../rail-state';

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

  /** "Task queued" is shown as a card above the composer while it's active, not in the chat. */
  const isQueuedNote = (entry: TranscriptItem) => entry.role === 'system' && entry.text.startsWith('Task queued');
  /** "Task completed/failed/cancelled": the one line a task leaves in the chat. */
  const isTaskOutcome = (entry: TranscriptItem) => entry.role === 'system' && !!entry.taskId && !isQueuedNote(entry);
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
      {#if isQueuedNote(entry)}
        <!-- Kept for screen readers (and scripts); the live card sits above the composer. -->
        <div class="entry system sr-only" data-role="system" data-turn-id={entry.turnId}>{entry.text}</div>
      {:else if entry.role === 'system'}
        {@const [head, rest] = splitSystem(entry.text)}
        {@const t = entry.taskId ? taskById.get(entry.taskId) : undefined}
        {@const steps = entry.taskId ? (taskSteps[entry.taskId] ?? []) : []}
        {@const hasDetail = !!rest || (t?.files.length ?? 0) > 0 || steps.length > 0}
        <div
          class={cn('entry system sc-system-line text-xs', /^Task failed/.test(entry.text) && 'failed')}
          data-role="system"
          data-turn-id={entry.turnId}
          data-streaming={entry.streaming}
        >
          {#if hasDetail}
            <details class="group">
              <summary class="flex cursor-pointer list-none items-start gap-2 hover:text-foreground [&::-webkit-details-marker]:hidden">
                {#if isTaskOutcome(entry)}<ClaudeMark size={14} class="mt-0.5" />{/if}
                <span class="min-w-0 flex-1">{head}</span>
                <ChevronDownIcon class="mt-px size-3.5 shrink-0 transition-transform group-open:rotate-180" />
              </summary>
              <div class="sc-system-detail mt-2 flex flex-col gap-1.5 border-l-2 pl-3">
                {#if rest}<p class="whitespace-pre-wrap text-foreground">{rest}</p>{/if}
                {#if t && t.files.length > 0}
                  <div class="flex flex-wrap gap-1">
                    {#each t.files as f (f)}<code class="rounded border bg-muted px-1.5 py-0.5 text-[11px]">{f}</code>{/each}
                  </div>
                {/if}
                {#each steps as step, i (i)}<p class="step" data-kind={step.kind}>{step.text}</p>{/each}
              </div>
            </details>
          {:else}
            <div class="flex items-start gap-2">
              {#if isTaskOutcome(entry)}<ClaudeMark size={14} class="mt-0.5" />{/if}
              <span class="min-w-0">{head}</span>
            </div>
          {/if}
        </div>
      {:else if entry.role === 'user'}
        <Message.Root from="user" class="entry user" data-role="user" data-turn-id={entry.turnId} data-streaming={entry.streaming}>
          <Message.Content class="sc-bubble-user whitespace-pre-wrap">
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
        <Message.Root from="assistant" class="entry agent sc-message-agent" data-role="agent" data-turn-id={entry.turnId} data-streaming={entry.streaming}>
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
        <Message.Content class="sc-bubble-user opacity-60">{partial}</Message.Content>
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
