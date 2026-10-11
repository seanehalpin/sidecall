<script lang="ts">
  import MicIcon from '@lucide/svelte/icons/mic';
  import MicOffIcon from '@lucide/svelte/icons/mic-off';
  import PhoneIcon from '@lucide/svelte/icons/phone';
  import PhoneOffIcon from '@lucide/svelte/icons/phone-off';
  import SettingsIcon from '@lucide/svelte/icons/settings';
  import { Button } from '$lib/components/ui/button';
  import * as Tooltip from '$lib/components/ui/tooltip';
  import { cn } from '$lib/utils';
  import { callLabel, type RailState } from '../rail-state';
  import ConnectionDot from './ConnectionDot.svelte';

  let {
    rail,
    canCall,
    onsettings,
    oncall,
    onhangup,
    onmute,
  }: {
    rail: RailState;
    /** Connected and bound to a repo, so a call can start. */
    canCall: boolean;
    onsettings: () => void;
    oncall: () => void;
    onhangup: () => void;
    onmute: (muted: boolean) => void;
  } = $props();

  const label = $derived(callLabel(rail));
  const title = $derived(rail.repo ? rail.repo.name : 'Sidecall');
  const subtitle = $derived(rail.repo ? rail.repo.path : (rail.origin ?? 'No local app'));
</script>

<header class="flex items-center gap-2 border-b bg-background px-3 py-2.5">
  <Tooltip.Root>
    <Tooltip.Trigger class="min-w-0 flex-1 cursor-default text-left">
      <div class="name truncate font-heading text-sm leading-tight font-semibold">{title}</div>
      <div class="path truncate text-left text-xs text-muted-foreground" style="direction: rtl">
        <span dir="ltr">{subtitle}</span>
      </div>
    </Tooltip.Trigger>
    <Tooltip.Content class="max-w-[320px] break-all whitespace-pre-line">{rail.repo ? `${rail.repo.name}\n${rail.repo.path}` : subtitle}</Tooltip.Content>
  </Tooltip.Root>

  {#if !rail.inCall}
    <ConnectionDot status={rail.connection} />
  {/if}

  {#if rail.inCall}
    {#if label}
      <span
        class="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground"
        data-call-state={rail.callState}
        data-muted={rail.muted}
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
            onclick={() => onmute(!rail.muted)}
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
          <Button {...props} variant="destructive" size="icon-sm" onclick={onhangup} aria-label="End call">
            <PhoneOffIcon />
          </Button>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content>End call</Tooltip.Content>
    </Tooltip.Root>
  {:else}
    <Tooltip.Root>
      <Tooltip.Trigger>
        {#snippet child({ props })}
          <Button {...props} variant="ghost" size="icon-sm" disabled={!canCall} onclick={oncall} aria-label="Start call">
            <PhoneIcon />
          </Button>
        {/snippet}
      </Tooltip.Trigger>
      <Tooltip.Content>Start a voice call</Tooltip.Content>
    </Tooltip.Root>
  {/if}

  <Tooltip.Root>
    <Tooltip.Trigger>
      {#snippet child({ props })}
        <Button {...props} variant="ghost" size="icon-sm" onclick={onsettings} aria-label="Settings">
          <SettingsIcon />
        </Button>
      {/snippet}
    </Tooltip.Trigger>
    <Tooltip.Content>Settings</Tooltip.Content>
  </Tooltip.Root>
</header>
