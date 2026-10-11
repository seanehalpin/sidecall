<script lang="ts">
  import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
  import LoaderIcon from '@lucide/svelte/icons/loader-circle';
  import MonitorOffIcon from '@lucide/svelte/icons/monitor-off';
  import PlugZapIcon from '@lucide/svelte/icons/plug-zap';
  import XIcon from '@lucide/svelte/icons/x';
  import { ModeWatcher } from 'mode-watcher';
  import { browser } from 'wxt/browser';
  import { Alert } from '$lib/components/ui/alert';
  import { Button } from '$lib/components/ui/button';
  import * as Card from '$lib/components/ui/card';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { RAIL_PORT } from '@/lib/messaging';
  import { createRailClient } from '@/lib/rail-client.svelte';
  import { canSend, railView } from '@/lib/rail-state';
  import Approvals from '@/lib/ui/Approvals.svelte';
  import Composer from '@/lib/ui/Composer.svelte';
  import Header from '@/lib/ui/Header.svelte';
  import PickTray from '@/lib/ui/PickTray.svelte';
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

  const thinking = $derived(rail.inCall && rail.callState === 'thinking');

  let composer: Composer | undefined = $state();

  const SUGGESTIONS = ['Make the heading bigger', "What's on this page?", 'Tidy up the spacing'];
  const showSuggestions = $derived(view === 'main' && rail.transcript.length === 0 && !rail.partial);

  function openSettings() {
    void browser.runtime.openOptionsPage();
  }
</script>

<ModeWatcher disableHeadScriptInjection />
<Tooltip.Provider delayDuration={300}>
  <div class="rail flex h-screen flex-col bg-background text-foreground" data-view={view}>
    <Header
      {rail}
      canCall={sendable}
      onsettings={openSettings}
      oncall={() => client.command({ cmd: 'callStart' })}
      onhangup={() => client.command({ cmd: 'callEnd' })}
      onmute={(muted) => client.command({ cmd: 'callMute', muted })}
    />

    {#if view === 'main'}
      {#if rail.callError}
        <Alert variant="destructive" class="banner m-3 mb-0 flex w-auto items-center justify-between gap-2 py-2" data-call-error={rail.callError.code}>
          <span class="flex items-center gap-2 text-xs"><CircleAlertIcon class="size-4 shrink-0" />{rail.callError.message}</span>
          <span class="actions flex flex-none items-center gap-1">
            {#if rail.callError.code === 'mic_permission'}
              <Button variant="outline" size="xs" onclick={openSettings}>Open settings</Button>
            {/if}
            <Button variant="ghost" size="icon-xs" onclick={() => client.command({ cmd: 'clearError' })} aria-label="Dismiss"><XIcon /></Button>
          </span>
        </Alert>
      {/if}
      {#if banner}
        <Alert variant="destructive" class="banner m-3 mb-0 flex w-auto items-center justify-between gap-2 py-2">
          <span class="flex items-center gap-2 text-xs"><CircleAlertIcon class="size-4 shrink-0" />{banner.message}</span>
          <Button variant="ghost" size="icon-xs" onclick={() => client.command({ cmd: 'clearError' })} aria-label="Dismiss"><XIcon /></Button>
        </Alert>
      {/if}
      <Transcript
        entries={rail.transcript}
        partial={rail.partial}
        tasks={rail.tasks}
        taskSteps={rail.taskSteps}
        picks={rail.picks}
        pickHistory={rail.pickHistory}
        {thinking}
        onhighlight={(id) => client.command({ cmd: 'pickHighlight', id })}
      />
    {:else}
      <div class="blocking flex-1 overflow-y-auto p-4">
        <Card.Root>
          <Card.Content class="flex flex-col gap-3">
            {#if view === 'offline'}
              <div class="flex items-center gap-2">
                {#if rail.connection === 'connecting'}
                  <LoaderIcon class="size-4 animate-spin text-muted-foreground" />
                {:else}
                  <PlugZapIcon class="size-4 text-muted-foreground" />
                {/if}
                <h2 class="m-0 font-heading text-base font-semibold">
                  {rail.connection === 'connecting' ? 'Connecting to the daemon…' : 'Daemon offline'}
                </h2>
              </div>
              <p class="m-0 text-muted-foreground">Start the daemon:</p>
              <pre class="m-0 rounded-xl bg-muted px-3 py-2 font-mono text-xs whitespace-pre-wrap select-all">cd daemon && uv run sidecall start</pre>
              <p class="m-0 text-xs text-muted-foreground">Looking on port {client.daemonPort}.</p>
              <Button variant="outline" class="self-start" onclick={() => client.command({ cmd: 'reconnect' })}>Retry now</Button>
            {:else if view === 'pairing'}
              <h2 class="m-0 font-heading text-base font-semibold">Pair with the daemon</h2>
              <Pairing error={rail.lastError} onpair={(code) => client.command({ cmd: 'pair', code })} />
            {:else if view === 'no_origin'}
              <div class="flex items-center gap-2">
                <MonitorOffIcon class="size-4 text-muted-foreground" />
                <h2 class="m-0 font-heading text-base font-semibold">No local app</h2>
              </div>
              <p class="m-0 text-muted-foreground">Open your local app (localhost) in this window.</p>
            {:else if view === 'repo_unknown' && rail.repoUnknown}
              {@const origin = rail.repoUnknown}
              <RepoSetup
                {origin}
                error={rail.lastError}
                onmap={(path) => client.send({ type: 'repo.map', payload: { origin, path } })}
                onbrowse={() => client.send({ type: 'repo.browse', payload: { origin } })}
              />
            {:else}
              <p class="m-0 flex items-center gap-2 text-muted-foreground">
                <LoaderIcon class="size-4 animate-spin" />Connecting to your repo…
              </p>
            {/if}
          </Card.Content>
        </Card.Root>
      </div>
    {/if}

    <div class="flex flex-none flex-col gap-2 p-3">
      {#if view === 'main'}
        <Approvals
          approvals={rail.approvals}
          disabled={!sendable}
          onrespond={(id, allow) => client.send({ type: 'approval.respond', payload: { id, allow } })}
        />
        <TaskQueue
          tasks={rail.tasks}
          lastTaskEvent={rail.lastTaskEvent}
          disabled={!sendable}
          oncancel={(id) => client.send({ type: 'task.cancel', payload: { id } })}
          onreorder={(ids) => client.send({ type: 'task.reorder', payload: { ids } })}
        />
      {/if}
      <Composer
        bind:this={composer}
        disabled={!sendable}
        showPickHint={view === 'main' && rail.picks.length === 0}
        pickedName={view === 'main' ? (rail.picks.find((p) => !p.dimmed)?.component ?? null) : null}
        inCall={rail.inCall}
        suggestions={showSuggestions ? SUGGESTIONS : []}
        header={view === 'main' && rail.picks.length > 0 ? pickTray : undefined}
        onsend={(text) => client.send({ type: 'user.text', payload: { text, pickIds: [], page: null } })}
      />
    </div>
  </div>
</Tooltip.Provider>

{#snippet pickTray()}
  <PickTray
    picks={rail.picks}
    onhover={(id) => client.command({ cmd: 'pickHighlight', id })}
    onremove={(id) => client.command({ cmd: 'pickRemove', id })}
    onclear={() => client.command({ cmd: 'picksClear' })}
  />
{/snippet}
