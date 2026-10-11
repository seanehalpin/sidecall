<script lang="ts">
  import SparklesIcon from '@lucide/svelte/icons/sparkles';
  import * as Conversation from '$lib/components/ai-elements/conversation';
  import * as Message from '$lib/components/ai-elements/message';
  import { Shimmer } from '$lib/components/ai-elements/shimmer';
  import { Suggestion, Suggestions } from '$lib/components/ai-elements/suggestion';
  import { badgeVariants } from '$lib/components/ui/badge';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { cn } from '$lib/utils';
  import type { PickChip } from '../picker-protocol';
  import { segmentMarkers } from '../picks/markers';
  import type { PickHistoryEntry, TranscriptItem } from '../rail-state';

  let {
    entries,
    partial,
    picks = [],
    pickHistory = {},
    thinking = false,
    onhighlight = () => {},
    onsuggest = () => {},
  }: {
    entries: TranscriptItem[];
    partial: string | null;
    picks?: PickChip[];
    pickHistory?: Record<number, PickHistoryEntry>;
    /** A voice turn is being worked on and no reply text has started streaming yet. */
    thinking?: boolean;
    onhighlight?: (id: number | null) => void;
    onsuggest?: (text: string) => void;
  } = $props();

  const SUGGESTIONS = ['Make the heading red', 'Add a dark mode toggle', 'Tidy up the spacing'];

  const liveIds = $derived(new Set(picks.map((p) => p.id)));
  const streamingAgent = $derived(entries.some((e) => e.role === 'agent' && e.streaming));
  const showThinking = $derived(thinking && !streamingAgent);
</script>

<Conversation.Root class="min-h-0 flex-1" aria-live="polite">
  <Conversation.Content class="gap-3 overflow-y-auto p-3">
    {#if entries.length === 0 && !partial}
      <div class="empty m-auto flex flex-col items-center gap-3 py-6 text-center">
        <div class="flex size-9 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <SparklesIcon class="size-4" />
        </div>
        <p class="max-w-[240px] text-sm text-muted-foreground">Type below to talk to the agent about your app.</p>
        <Suggestions class="flex-wrap justify-center whitespace-normal">
          {#each SUGGESTIONS as s (s)}
            <Suggestion suggestion={s} size="xs" onclick={onsuggest} />
          {/each}
        </Suggestions>
      </div>
    {/if}
    {#each entries as entry (entry.role + ':' + entry.turnId)}
      {#if entry.role === 'system'}
        <div
          class="entry system flex items-center justify-center gap-2 text-center text-xs text-muted-foreground"
          data-role="system"
          data-turn-id={entry.turnId}
          data-streaming={entry.streaming}
        >
          <span class="h-px w-4 bg-border"></span>
          <span>{entry.text}</span>
          <span class="h-px w-4 bg-border"></span>
        </div>
      {:else}
        <Message.Root
          from={entry.role === 'user' ? 'user' : 'assistant'}
          class={cn('entry', entry.role, entry.role === 'agent' && 'mr-auto')}
          data-role={entry.role}
          data-turn-id={entry.turnId}
          data-streaming={entry.streaming}
        >
          <Message.Content
            class={cn(
              'bubble leading-relaxed break-words whitespace-pre-wrap',
              entry.role === 'user'
                ? 'group-[.is-user]:rounded-2xl group-[.is-user]:rounded-br-md group-[.is-user]:bg-primary group-[.is-user]:px-3 group-[.is-user]:py-2 group-[.is-user]:text-primary-foreground'
                : 'w-fit rounded-2xl rounded-bl-md bg-muted px-3 py-2',
            )}
          >
            {#if entry.role === 'agent' && entry.streaming && !entry.text}
              <Shimmer as="span">Thinking…</Shimmer>
            {:else}
              <p class="m-0">{#if entry.role === 'user'}{#each segmentMarkers(entry.text) as seg, i (i)}{#if seg.kind === 'marker' && pickHistory[seg.id]}{@const known = pickHistory[seg.id]}{@const live = liveIds.has(seg.id)}{@const tip = `${known?.label ?? ''}\n${known?.tooltip ?? ''}`}<Tooltip.Root><Tooltip.Trigger>{#snippet child({ props })}<span {...props} class={cn(badgeVariants({ variant: 'outline' }), 'marker mx-0.5 border-primary-foreground/40 bg-primary-foreground/15 font-mono text-[11px] text-primary-foreground', live ? 'cursor-pointer' : 'cursor-default', live && 'live')} data-marker={seg.id} data-tip={tip} role="presentation" onmouseenter={() => live && onhighlight(seg.id)} onmouseleave={() => live && onhighlight(null)}>{seg.raw}</span>{/snippet}</Tooltip.Trigger><Tooltip.Content class="whitespace-pre-line">{tip}</Tooltip.Content></Tooltip.Root>{:else if seg.kind === 'marker'}{seg.raw}{:else}{seg.text}{/if}{/each}{:else}{entry.text}{/if}{#if entry.streaming}<span class="caret ml-0.5 inline-block h-[1em] w-1.5 animate-pulse bg-current align-text-bottom opacity-50" aria-hidden="true"></span>{/if}</p>
            {/if}
          </Message.Content>
        </Message.Root>
      {/if}
    {/each}
    {#if partial}
      <Message.Root from="user" class="entry user partial" data-role="user">
        <Message.Content
          class="bubble rounded-2xl rounded-br-md bg-primary/60 px-3 py-2 text-primary-foreground italic group-[.is-user]:rounded-2xl group-[.is-user]:bg-primary/60 group-[.is-user]:px-3 group-[.is-user]:py-2 group-[.is-user]:text-primary-foreground"
        >{partial}</Message.Content
        >
      </Message.Root>
    {/if}
    {#if showThinking}
      <div class="thinking text-xs" role="status"><Shimmer as="span">Thinking…</Shimmer></div>
    {/if}
  </Conversation.Content>
  <Conversation.ScrollButton />
</Conversation.Root>
