<script lang="ts">
  import { tick } from 'svelte';
  import { insertAtRange } from '../picks/insert';

  let {
    disabled,
    showPickHint = false,
    inCall = false,
    onsend,
  }: { disabled: boolean; showPickHint?: boolean; inCall?: boolean; onsend: (text: string) => void } = $props();

  let text = $state('');
  let field: HTMLTextAreaElement | undefined = $state();

  function submit() {
    const value = text.trim();
    if (!value || disabled) return;
    onsend(value);
    text = '';
  }

  function onkeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
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

<form
  class="composer"
  onsubmit={(e) => {
    e.preventDefault();
    submit();
  }}
>
  <textarea
    bind:this={field}
    bind:value={text}
    {onkeydown}
    {disabled}
    rows="2"
    placeholder={disabled ? 'Connect to a repo to start' : 'Message the agent (Enter to send, Shift+Enter for a new line)'}
    aria-label="Message"
  ></textarea>
  <div class="row">
    <span class="hint muted">
      {#if inCall}
        <span class="call-hint">On a call: just talk, or type here</span>
      {:else if showPickHint}
        <span class="pick-hint">⌥-click elements on the page, then refer to them as [1], [2]</span>
      {:else}
        Type your request, or start a voice call from the phone button
      {/if}
    </span>
    <button class="primary" type="submit" disabled={disabled || text.trim() === ''}>Send</button>
  </div>
</form>

<style>
  .composer {
    border-top: 1px solid var(--border);
    padding: 8px 12px 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    flex: none;
  }
  textarea {
    width: 100%;
    resize: none;
    min-height: 44px;
    max-height: 160px;
    field-sizing: content;
  }
  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
  }
  .hint {
    font-size: 11px;
  }
</style>
