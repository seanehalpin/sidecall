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
    <span class="mic muted" title="Voice arrives in Milestone 3">
      <svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true">
        <path
          fill="currentColor"
          d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11z"
        />
        <line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" stroke-width="2" />
      </svg>
      Mic off
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
  }
  .mic {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 11px;
  }
</style>
