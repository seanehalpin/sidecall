<script lang="ts">
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

<main>
  <h1>Sidecall settings</h1>

  <section>
    <h2>Connection</h2>
    <p class="status" data-connection={rail.connection}>
      <ConnectionDot status={rail.connection} />
      <span>{statusText}</span>
    </p>
    <form onsubmit={savePort}>
      <label for="port">Daemon port</label>
      <input
        id="port"
        type="number"
        min="1"
        max="65535"
        value={portInput}
        oninput={(e) => (portEdit = e.currentTarget.value)}
      />
      <button type="submit" disabled={parsedPort === null || parsedPort === client.daemonPort}>Save</button>
      {#if saved}<span class="muted">Saved</span>{/if}
    </form>
  </section>

  <section>
    <h2>Microphone</h2>
    <p class="status" data-mic={mic}>{micText}</p>
    <p class="muted">Voice calls need the microphone. Chrome can only ask for it here, so grant it once.</p>
    {#if mic !== 'granted'}
      <button class="primary" onclick={grantMic} disabled={micBusy}>Grant microphone</button>
    {/if}
    {#if micError}<p class="error-text" role="alert">{micError}</p>{/if}
  </section>

  <section>
    <h2>Pairing</h2>
    {#if rail.paired}
      <p>This browser is paired with the daemon.</p>
      <button onclick={() => client.command({ cmd: 'forgetPairing' })}>Forget pairing</button>
    {:else}
      <Pairing
        error={rail.lastError}
        disabled={rail.connection !== 'connected'}
        onpair={(code) => client.command({ cmd: 'pair', code })}
      />
      {#if rail.connection !== 'connected'}
        <p class="muted">Start the daemon to pair: <code>cd daemon && uv run sidecall start</code></p>
      {/if}
    {/if}
  </section>
</main>

<style>
  main {
    max-width: 560px;
    margin: 32px auto;
    padding: 0 16px;
  }
  h1 {
    font-size: 20px;
  }
  h2 {
    font-size: 14px;
    margin: 0 0 8px;
  }
  section {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px 16px;
    margin-bottom: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  section > button {
    align-self: flex-start;
  }
  p {
    margin: 0;
  }
  .status {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  form {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  input[type='number'] {
    width: 9ch;
  }
  code {
    font-family: var(--mono);
    font-size: 12px;
  }
</style>
