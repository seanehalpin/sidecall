<script lang="ts">
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
  import { Label } from '$lib/components/ui/label';
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

<form class="pairing flex flex-col gap-2" onsubmit={submit}>
  <Label for="pairing-code">Pairing code</Label>
  <p class="hint m-0 text-xs text-muted-foreground">
    {#if shownError?.code === 'unauthorized'}
      The daemon no longer accepts this browser's token. Pair again with the code printed by
      <code class="font-mono">sidecall start</code>.
    {:else}
      Enter the 6-digit code printed by <code class="font-mono">sidecall start</code>.
    {/if}
  </p>
  <div class="flex gap-2">
    <Input
      id="pairing-code"
      type="text"
      inputmode="numeric"
      autocomplete="one-time-code"
      maxlength={9}
      placeholder="123456"
      class="w-32 text-center font-mono tracking-widest"
      bind:value={code}
      {disabled}
    />
    <Button type="submit" disabled={disabled || code.trim() === ''}>Pair</Button>
  </div>
  {#if shownError && shownError.code !== 'unauthorized'}
    <p class="error-text m-0 text-xs text-destructive" role="alert">{shownError.message}</p>
  {/if}
</form>
