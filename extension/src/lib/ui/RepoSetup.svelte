<script lang="ts">
  import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
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

<div class="setup flex flex-col gap-3">
  <h2 class="m-0 font-heading text-base font-semibold">Which repo is this?</h2>
  <p class="m-0 text-muted-foreground">
    <code class="rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all">{origin}</code> isn't mapped to a folder yet.
  </p>
  <form class="flex gap-2" onsubmit={submit}>
    <Input type="text" bind:value={path} placeholder="/Users/you/code/my-app" aria-label="Repo path" class="min-w-0 flex-1" />
    <Button type="submit" disabled={path.trim() === ''}>Use path</Button>
  </form>
  <Button variant="outline" class="self-start" onclick={onbrowse}><FolderOpenIcon />Choose folder…</Button>
  {#if shownError}
    <p class="error-text m-0 text-xs text-destructive" role="alert">{shownError.message}</p>
  {/if}
</div>
