<script lang="ts">
  import { tick, type Snippet } from 'svelte';
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
  <PromptInput.Root onSubmit={submit}>
    {#if header}
      <PromptInput.Header>{@render header()}</PromptInput.Header>
    {/if}
    <PromptInput.Body>
      <PromptInput.Textarea
        bind:ref={field}
        bind:value={text}
        {disabled}
        placeholder={disabled ? 'Connect to a repo to start' : pickedName ? `Change ${pickedName}…` : 'Message the agent…'}
        aria-label="Message"
        class="text-sm"
      />
    </PromptInput.Body>
    <PromptInput.Toolbar>
      <PromptInput.Tools class="hint min-w-0 px-2 text-xs text-muted-foreground">
        {#if inCall}
          <span class="call-hint">On a call: just talk, or type here</span>
        {:else if showPickHint}
          <span class="pick-hint">⌥-click elements on the page to point at them</span>
        {:else}
          <span>Enter to send, Shift+Enter for a new line</span>
        {/if}
      </PromptInput.Tools>
      <PromptInput.Submit disabled={disabled || text.trim() === ''} />
    </PromptInput.Toolbar>
  </PromptInput.Root>
</div>
