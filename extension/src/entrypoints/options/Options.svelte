<script lang="ts">
  import { ModeWatcher } from 'mode-watcher';
  import { Button } from '$lib/components/ui/button';
  import * as Card from '$lib/components/ui/card';
  import { Input } from '$lib/components/ui/input';
  import { Label } from '$lib/components/ui/label';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { OPTIONS_PORT, parsePort } from '@/lib/messaging';
  import { createRailClient } from '@/lib/rail-client.svelte';
  import ConnectionDot from '@/lib/ui/ConnectionDot.svelte';
  import Pairing from '@/lib/ui/Pairing.svelte';

  const client = createRailClient(OPTIONS_PORT);
  const rail = $derived(client.state);

  // Seeded from the worker's value until the user edits the field.
  let portEdit: string | null = $state(null);
  const portInput = $derived(portEdit ?? String(client.daemonPort));
  const parsedPort = $derived(parsePort(portInput));
  let saved = $state(false);

  const statusText = $derived(
    rail.connection === 'connected'
      ? rail.paired
        ? 'Connected and paired'
        : 'Connected, not paired'
      : rail.connection === 'connecting'
        ? 'Connecting…'
        : 'Daemon offline',
  );

  // Microphone: only an extension page shown in a tab can prompt for it; the grant
  // then covers the offscreen call document too (SPEC §5.5).
  let mic = $state<PermissionState | 'unknown'>('unknown');
  let micError: string | null = $state(null);
  let micBusy = $state(false);

  $effect(() => {
    let status: PermissionStatus | null = null;
    const update = () => {
      if (status) mic = status.state;
    };
    navigator.permissions
      .query({ name: 'microphone' as PermissionName })
      .then((s) => {
        status = s;
        update();
        s.addEventListener('change', update);
      })
      .catch(() => (mic = 'unknown'));
    return () => status?.removeEventListener('change', update);
  });

  const micText = $derived(
    mic === 'granted'
      ? 'Microphone granted'
      : mic === 'denied'
        ? 'Microphone blocked. Allow it in Chrome’s site settings for this extension, then reload this page.'
        : 'Microphone not granted yet',
  );

  async function grantMic() {
    micBusy = true;
    micError = null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((t) => t.stop());
      mic = 'granted';
    } catch (err) {
      micError = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
      const status = await navigator.permissions.query({ name: 'microphone' as PermissionName }).catch(() => null);
      if (status) mic = status.state;
    } finally {
      micBusy = false;
    }
  }

  function savePort(e: SubmitEvent) {
    e.preventDefault();
    if (parsedPort === null) return;
    client.command({ cmd: 'setPort', port: parsedPort });
    portEdit = null;
    saved = true;
    setTimeout(() => (saved = false), 1500);
  }
</script>

<ModeWatcher disableHeadScriptInjection />
<Tooltip.Provider>
<main class="mx-auto flex min-h-full max-w-xl flex-col gap-4 bg-background px-4 py-8 text-foreground">
  <h1 class="m-0 font-heading text-xl font-semibold">On it settings</h1>

  <Card.Root>
    <Card.Header>
      <Card.Title>Connection</Card.Title>
    </Card.Header>
    <Card.Content class="flex flex-col gap-3">
      <p class="status m-0 flex items-center gap-2" data-connection={rail.connection}>
        <ConnectionDot status={rail.connection} compact />
        <span>{statusText}</span>
      </p>
      <form class="flex items-center gap-2" onsubmit={savePort}>
        <Label for="port">Daemon port</Label>
        <Input
          id="port"
          type="number"
          min="1"
          max="65535"
          class="w-28"
          value={portInput}
          oninput={(e) => (portEdit = e.currentTarget.value)}
        />
        <Button type="submit" disabled={parsedPort === null || parsedPort === client.daemonPort}>Save</Button>
        {#if saved}<span class="text-muted-foreground">Saved</span>{/if}
      </form>
    </Card.Content>
  </Card.Root>

  <Card.Root>
    <Card.Header>
      <Card.Title>Microphone</Card.Title>
    </Card.Header>
    <Card.Content class="flex flex-col gap-3">
      <p class="status m-0" data-mic={mic}>{micText}</p>
      <p class="m-0 text-muted-foreground">Voice calls need the microphone. Chrome can only ask for it here, so grant it once.</p>
      {#if mic !== 'granted'}
        <Button class="self-start" onclick={grantMic} disabled={micBusy}>Grant microphone</Button>
      {/if}
      {#if micError}<p class="error-text m-0 text-destructive" role="alert">{micError}</p>{/if}
    </Card.Content>
  </Card.Root>

  <Card.Root>
    <Card.Header>
      <Card.Title>Pairing</Card.Title>
    </Card.Header>
    <Card.Content class="flex flex-col gap-3">
      {#if rail.paired}
        <p class="m-0">This browser is paired with the daemon.</p>
        <Button variant="outline" class="self-start" onclick={() => client.command({ cmd: 'forgetPairing' })}>Forget pairing</Button>
      {:else}
        <Pairing
          error={rail.lastError}
          disabled={rail.connection !== 'connected'}
          onpair={(code) => client.command({ cmd: 'pair', code })}
        />
        {#if rail.connection !== 'connected'}
          <p class="m-0 text-muted-foreground">
            Start the daemon to pair: <code class="font-mono text-xs">cd daemon && uv run sidecall start</code>
          </p>
        {/if}
      {/if}
    </Card.Content>
  </Card.Root>
</main>
</Tooltip.Provider>
