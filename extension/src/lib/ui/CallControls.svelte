<script lang="ts">
  // The call controls (status, call/mute/hang up, with their sounds), shown in the message box.
  import { play, setTheme, type PlayOptions, type SoundName } from 'cuelume';
  import MicIcon from '@lucide/svelte/icons/mic';
  import MicOffIcon from '@lucide/svelte/icons/mic-off';
  import PhoneIcon from '@lucide/svelte/icons/phone';
  import PhoneOffIcon from '@lucide/svelte/icons/phone-off';
  import { Button } from '$lib/components/ui/button';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { cn } from '$lib/utils';
  import { callLabel, type RailState } from '../rail-state';
  import ConnectionDot from './ConnectionDot.svelte';

  let {
    rail,
    canCall,
    oncall,
    onhangup,
    onmute,
  }: {
    rail: RailState;
    /** Connected and bound to a repo, so a call can start. */
    canCall: boolean;
    oncall: () => void;
    onhangup: () => void;
    onmute: (muted: boolean) => void;
  } = $props();

  const label = $derived(callLabel(rail));

  // Interaction sounds for the call controls only (cuelume's "press" theme).
  setTheme('press');
  /** Every call sound is subtle. */
  const cue = (name: SoundName, opts: PlayOptions = {}) => play(name, { emphasis: 'subtle', ...opts });
  const OUTCOME_GAP_MS = 400; // the click and the outcome never run together

  // Starting a call: a click now, then success once it's listening, or an error if it fails.
  let pendingSince: number | null = $state(null);
  function startCall() {
    cue('tap');
    pendingSince = Date.now();
    oncall();
  }
  function outcome(result: 'success' | 'error') {
    const wait = Math.max(0, OUTCOME_GAP_MS - (Date.now() - (pendingSince ?? 0)));
    pendingSince = null;
    setTimeout(() => cue(result), wait);
  }
  $effect(() => {
    if (pendingSince === null) return;
    if (rail.callError) outcome('error');
    else if (rail.callState === 'listening') outcome('success');
  });

  // Hanging up: just the click.
  function hangUp() {
    pendingSince = null;
    cue('tap');
    onhangup();
  }
  function toggleMute() {
    const muting = !rail.muted;
    cue('toggle', { direction: muting ? 'forward' : 'back' });
    onmute(muting);
  }
</script>

{#if rail.inCall}
  <!-- Everything a live call adds to the message box, grouped for styling (.sc-call in components.css). -->
  <div class="sc-call flex shrink-0 items-center gap-1" data-call-state={rail.callState} data-muted={rail.muted}>
    {#if label}
      <span
        class="sc-call-state inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground pr-2"
        aria-live="polite"
      >
        <span
          class={cn(
            'size-1.5 rounded-full bg-muted-foreground',
            rail.callState === 'listening' && !rail.muted && 'bg-emerald-500',
            rail.callState === 'listening' && rail.muted && 'bg-destructive',
            rail.callState === 'thinking' && 'animate-pulse bg-amber-500',
            rail.callState === 'speaking' && 'animate-pulse bg-primary',
          )}
          aria-hidden="true"
        ></span>{label}
      </span>
    {/if}
    <Tooltip.Root>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <Button
            {...props}
            variant={rail.muted ? 'destructive' : 'ghost'}
            size="icon-sm"
            onclick={toggleMute}
            aria-pressed={rail.muted}
            aria-label={rail.muted ? 'Unmute' : 'Mute'}
          >
            {#if rail.muted}<MicOffIcon />{:else}<MicIcon />{/if}
          </Button>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content>{rail.muted ? 'Unmute' : 'Mute'}</Tooltip.Content>
    </Tooltip.Root>
    <Tooltip.Root>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <Button {...props} variant="destructive" size="icon-sm" onclick={hangUp} aria-label="End call">
            <PhoneOffIcon />
          </Button>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content>End call</Tooltip.Content>
    </Tooltip.Root>
  </div>
{:else}
  <!-- Not on a call: the status dot and the call button, grouped like .sc-call. -->
  <div class="sc-call sc-call-idle flex shrink-0 items-center gap-1">
    <ConnectionDot status={rail.connection} />
    <Tooltip.Root>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <Button {...props} variant="ghost" size="icon-sm" disabled={!canCall} onclick={startCall} aria-label="Start call">
            <PhoneIcon />
          </Button>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content>Start a voice call</Tooltip.Content>
    </Tooltip.Root>
  </div>
{/if}
