<script lang="ts">
  import { browser } from 'wxt/browser';
  import { RAIL_PORT } from '@/lib/messaging';
  import { createRailClient } from '@/lib/rail-client.svelte';
  import { canSend, railView } from '@/lib/rail-state';
  import Approvals from '@/lib/ui/Approvals.svelte';
  import Composer from '@/lib/ui/Composer.svelte';
  import Header from '@/lib/ui/Header.svelte';
  import Pairing from '@/lib/ui/Pairing.svelte';
  import RepoSetup from '@/lib/ui/RepoSetup.svelte';
  import TaskQueue from '@/lib/ui/TaskQueue.svelte';
  import Transcript from '@/lib/ui/Transcript.svelte';

  const client = createRailClient(RAIL_PORT);

  const rail = $derived(client.state);
  const view = $derived(railView(rail));
  const sendable = $derived(canSend(rail));
  // Errors with their own inline place (pairing, repo setup) aren't bannered.
  const INLINE_ERRORS = new Set(['bad_pairing_code', 'unauthorized', 'bad_path', 'browse_cancelled', 'no_origin']);
  const banner = $derived(view === 'main' && rail.lastError && !INLINE_ERRORS.has(rail.lastError.code) ? rail.lastError : null);

  function openSettings() {
    void browser.runtime.openOptionsPage();
  }
</script>

<div class="rail" data-view={view}>
  <Header {rail} onsettings={openSettings} />

  {#if view === 'main'}
    <Approvals
      approvals={rail.approvals}
      disabled={!sendable}
      onrespond={(id, allow) => client.send({ type: 'approval.respond', payload: { id, allow } })}
    />
    {#if banner}
      <div class="banner" role="alert">
        <span>{banner.message}</span>
        <button onclick={() => client.command({ cmd: 'clearError' })} aria-label="Dismiss">×</button>
      </div>
    {/if}
    <Transcript entries={rail.transcript} partial={rail.partial} />
    <TaskQueue
      tasks={rail.tasks}
      lastTaskEvent={rail.lastTaskEvent}
      disabled={!sendable}
      oncancel={(id) => client.send({ type: 'task.cancel', payload: { id } })}
      onreorder={(ids) => client.send({ type: 'task.reorder', payload: { ids } })}
    />
    <!-- Pick tray: Milestone 2. -->
  {:else}
    <div class="blocking">
      {#if view === 'offline'}
        <h2>{rail.connection === 'connecting' ? 'Connecting to the daemon…' : 'Daemon offline'}</h2>
        <p class="muted">Start the daemon:</p>
        <pre>cd daemon && uv run sidecall start</pre>
        <p class="muted small">Looking on port {client.daemonPort}.</p>
        <button onclick={() => client.command({ cmd: 'reconnect' })}>Retry now</button>
      {:else if view === 'pairing'}
        <h2>Pair with the daemon</h2>
        <Pairing error={rail.lastError} onpair={(code) => client.command({ cmd: 'pair', code })} />
      {:else if view === 'no_origin'}
        <h2>No local app</h2>
        <p class="muted">Open your local app (localhost) in this window.</p>
      {:else if view === 'repo_unknown' && rail.repoUnknown}
        {@const origin = rail.repoUnknown}
        <RepoSetup
          {origin}
          error={rail.lastError}
          onmap={(path) => client.send({ type: 'repo.map', payload: { origin, path } })}
          onbrowse={() => client.send({ type: 'repo.browse', payload: { origin } })}
        />
      {:else}
        <p class="muted">Connecting to your repo…</p>
      {/if}
    </div>
  {/if}

  <Composer
    disabled={!sendable}
    onsend={(text) => client.send({ type: 'user.text', payload: { text, pickIds: [], page: null } })}
  />
</div>

<style>
  .rail {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  .blocking {
    flex: 1;
    padding: 24px 16px;
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
  .small {
    font-size: 11px;
  }
  pre {
    margin: 0;
    padding: 8px 10px;
    background: var(--bg-subtle);
    border-radius: 6px;
    font-family: var(--mono);
    font-size: 12px;
    white-space: pre-wrap;
    user-select: all;
  }
  .blocking button {
    align-self: flex-start;
  }
  .banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 12px;
    background: var(--warn-bg);
    border-bottom: 1px solid var(--warn-border);
    font-size: 12px;
  }
  .banner button {
    border: none;
    background: transparent;
    padding: 0 4px;
  }
</style>
