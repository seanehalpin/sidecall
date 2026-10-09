<script lang="ts">
  import type { RailError } from '../rail-state';

  let {
    error,
    disabled = false,
    onpair,
  }: { error: RailError | null; disabled?: boolean; onpair: (code: string) => void } = $props();

  const PAIRING_ERRORS = new Set(['bad_pairing_code', 'unauthorized', 'not_connected']);

  let code = $state('');
  const shownError = $derived(error && PAIRING_ERRORS.has(error.code) ? error : null);

  function submit(e: SubmitEvent) {
    e.preventDefault();
    if (code.trim()) onpair(code.trim());
  }
</script>

<form class="pairing" onsubmit={submit}>
  <label for="pairing-code">Pairing code</label>
  <p class="muted hint">
    {#if shownError?.code === 'unauthorized'}
      The daemon no longer accepts this browser's token. Pair again with the code printed by
      <code>sidecall start</code>.
    {:else}
      Enter the 6-digit code printed by <code>sidecall start</code>.
    {/if}
  </p>
  <div class="row">
    <input
      id="pairing-code"
      type="text"
      inputmode="numeric"
      autocomplete="one-time-code"
      maxlength="9"
      placeholder="123456"
      bind:value={code}
      {disabled}
    />
    <button class="primary" type="submit" disabled={disabled || code.trim() === ''}>Pair</button>
  </div>
  {#if shownError && shownError.code !== 'unauthorized'}
    <p class="error-text" role="alert">{shownError.message}</p>
  {/if}
</form>

<style>
  .pairing {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  label {
    font-weight: 600;
  }
  .hint {
    margin: 0;
  }
  .row {
    display: flex;
    gap: 6px;
  }
  input {
    width: 9ch;
    font-family: var(--mono);
    letter-spacing: 0.1em;
  }
  p {
    margin: 0;
  }
  code {
    font-family: var(--mono);
    font-size: 12px;
  }
</style>
