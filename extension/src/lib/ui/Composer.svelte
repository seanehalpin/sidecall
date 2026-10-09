<script lang="ts">
  let { disabled, onsend }: { disabled: boolean; onsend: (text: string) => void } = $props();

  let text = $state('');

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
</script>

<form
  class="composer"
  onsubmit={(e) => {
    e.preventDefault();
    submit();
  }}
>
  <textarea
    bind:value={text}
    {onkeydown}
    {disabled}
    rows="2"
    placeholder={disabled ? 'Connect to a repo to start' : 'Message the agent (Enter to send, Shift+Enter for a new line)'}
    aria-label="Message"
  ></textarea>
  <div class="row">
    <span class="hint muted">Type your request · voice is coming in a later update</span>
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
  }
  .hint {
    font-size: 11px;
  }
</style>
