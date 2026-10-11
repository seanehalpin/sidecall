<script lang="ts">
  import { tick, type Snippet } from 'svelte';
  import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
  import { Button } from '$lib/components/ui/button';
  import * as PromptInput from '$lib/components/ai-elements/prompt-input';
  import { Suggestion, Suggestions } from '$lib/components/ai-elements/suggestion';
  import { insertAtRange } from '../picks/insert';

  let {
    disabled,
    showPickHint = false,
    inCall = false,
    pickedName = null,
    suggestions = [],
    header,
    leading,
    trailing,
    onsend,
  }: {
    disabled: boolean;
    showPickHint?: boolean;
    inCall?: boolean;
    /** Component of the first live pick: the placeholder becomes "Change Card…". */
    pickedName?: string | null;
    /** Starter prompts shown above the input (an empty conversation). */
    suggestions?: string[];
    /** Rendered in the PromptInput header (the pick chips). */
    header?: Snippet;
    /** Bottom-left of the box (the ⋯ repo menu). */
    leading?: Snippet;
    /** Bottom-right, before the send button (the call controls). */
    trailing?: Snippet;
    onsend: (text: string) => void;
  } = $props();

  let text = $state('');
  let field: HTMLTextAreaElement | null = $state(null);

  function submit(message: PromptInput.PromptInputMessage) {
    const value = message.text.trim();
    if (!value || disabled) return;
    onsend(value);
  }

  /**
   * Insert `snippet` (e.g. "[2]") at the caret. The textarea keeps its selection
   * while blurred (and sits at the end if never focused), so clicking a tray chip
   * lands where the user was typing.
   */
  export async function insertAtCursor(snippet: string) {
    if (disabled || !field) return;
    const result = insertAtRange(text, field.selectionStart, field.selectionEnd, snippet);
    text = result.text;
    await tick();
    field.focus();
    field.setSelectionRange(result.caret, result.caret);
  }
</script>

<div class="composer flex flex-col gap-2">
  {#if suggestions.length > 0 && !disabled}
    <Suggestions>
      {#each suggestions as s (s)}
        <Suggestion suggestion={s} onclick={(value) => void insertAtCursor(value)} />
      {/each}
    </Suggestions>
  {/if}
  <PromptInput.Root class="sc-composer" onSubmit={submit}>
    {#if header}
      <PromptInput.Header>{@render header()}</PromptInput.Header>
    {/if}
    <PromptInput.Body>
      <!-- On a call you talk instead: the field greys out and the send button goes. -->
      <PromptInput.Textarea
        bind:ref={field}
        bind:value={text}
        disabled={disabled || inCall}
        placeholder={disabled ? 'Connect to a repo to start' : pickedName ? `Change ${pickedName}…` : 'Message...'}
        aria-label="Message"
        class="text-sm"
      />
    </PromptInput.Body>
    <PromptInput.Toolbar class="sc-composer-toolbar">
      <div class="flex min-w-0 items-center gap-1">{@render leading?.()}</div>
      <div class="flex shrink-0 items-center gap-1">
        {@render trailing?.()}
        {#if !inCall}
          <Button
            type="submit"
            variant="ghost"
            size="icon-sm"
            class="sc-send"
            disabled={disabled || text.trim() === ''}
            aria-label="Send"
            aria-keyshortcuts="Enter"
            title="Send (Enter)"
          >
            <ArrowUpIcon />
          </Button>
        {/if}
      </div>
    </PromptInput.Toolbar>
  </PromptInput.Root>
</div>
