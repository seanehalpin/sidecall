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
