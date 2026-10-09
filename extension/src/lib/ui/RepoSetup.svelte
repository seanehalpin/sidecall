<script lang="ts">
  import type { RailError } from '../rail-state';

  let {
    origin,
    error,
    onmap,
    onbrowse,
  }: {
    origin: string;
    error: RailError | null;
    onmap: (path: string) => void;
    onbrowse: () => void;
  } = $props();

  // Pairing and binding errors have their own screens; show anything else here
  // (bad_path, browse_cancelled, bad_message, bad_origin, not_connected, …).
  const ELSEWHERE = new Set(['bad_pairing_code', 'unauthorized', 'no_origin']);

  let path = $state('');
  const shownError = $derived(error && !ELSEWHERE.has(error.code) ? error : null);

  function submit(e: SubmitEvent) {
    e.preventDefault();
    if (path.trim()) onmap(path.trim());
  }
</script>

<div class="setup">
  <h2>Which repo is this?</h2>
  <p class="muted"><code>{origin}</code> isn't mapped to a folder yet.</p>
  <form onsubmit={submit}>
    <input type="text" bind:value={path} placeholder="/Users/you/code/my-app" aria-label="Repo path" />
    <button class="primary" type="submit" disabled={path.trim() === ''}>Use path</button>
  </form>
  <button onclick={onbrowse}>Choose folder…</button>
  {#if shownError}
    <p class="error-text" role="alert">{shownError.message}</p>
  {/if}
</div>

<style>
  .setup {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  h2 {
    font-size: 15px;
    margin: 0;
  }
  p {
    margin: 0;
  }
  form {
    display: flex;
    gap: 6px;
  }
  input {
    flex: 1;
    min-width: 0;
  }
  .setup > button {
    align-self: flex-start;
  }
  code {
    font-family: var(--mono);
    font-size: 12px;
  }
</style>
