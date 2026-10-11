<script lang="ts">
  import { tick } from 'svelte';
  import * as PromptInput from '$lib/components/ai-elements/prompt-input';
  import { insertAtRange } from '../picks/insert';

  let {
    disabled,
    showPickHint = false,
    inCall = false,
    onsend,
  }: { disabled: boolean; showPickHint?: boolean; inCall?: boolean; onsend: (text: string) => void } = $props();

  let text = $state('');
  let field: HTMLTextAreaElement | null = $state(null);

  function submit() {
    const value = text.trim();
    if (!value || disabled) return;
    onsend(value);
    text = '';
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

<div class="composer flex-none border-t bg-card px-3 pt-2.5 pb-3">
  <PromptInput.Root onSubmit={submit}>
    <PromptInput.Body>
      <PromptInput.Textarea
        bind:ref={field}
        bind:value={text}
        {disabled}
        rows={1}
        placeholder={disabled ? 'Connect to a repo to start' : 'Message the agent…'}
        aria-label="Message"
      />
    </PromptInput.Body>
    <PromptInput.Toolbar class="gap-2 px-3 pb-2">
      <span class="hint min-w-0 text-xs text-muted-foreground">
        {#if inCall}
          <span class="call-hint">On a call: just talk, or type here</span>
        {:else if showPickHint}
          <span class="pick-hint">⌥-click elements on the page, then refer to them as [1], [2]</span>
        {:else}
          Type your request, or start a voice call from the phone button
        {/if}
      </span>
      <PromptInput.Submit class="shrink-0" disabled={disabled || text.trim() === ''} />
    </PromptInput.Toolbar>
  </PromptInput.Root>
</div>
