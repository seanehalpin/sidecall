<script lang="ts">
  import MicIcon from '@lucide/svelte/icons/mic';
  import MicOffIcon from '@lucide/svelte/icons/mic-off';
  import PhoneIcon from '@lucide/svelte/icons/phone';
  import PhoneOffIcon from '@lucide/svelte/icons/phone-off';
  import SettingsIcon from '@lucide/svelte/icons/settings';
  import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
  import * as DropdownMenu from '$lib/components/ui/dropdown-menu';
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

<header class="sc-header flex items-center gap-2 border-b p-4">
  <!-- Repo name as a menu: the path and settings live inside it, out of the way. -->
  <div class="min-w-0 flex-1">
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        class="sc-repo-trigger group inline-flex max-w-full min-w-0 cursor-pointer items-center gap-1 rounded-md px-1.5 py-1 -ml-1.5 text-left outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
        aria-label="Repo menu"
      >
        <span class="name truncate font-heading text-sm leading-tight font-semibold">{title}</span>
        <ChevronDownIcon class="size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="start" class="sc-repo-menu w-64">
        <DropdownMenu.Label class="path text-xs font-normal break-all text-muted-foreground">{subtitle}</DropdownMenu.Label>
        <DropdownMenu.Separator />
        <DropdownMenu.Item onSelect={onsettings}>
          <SettingsIcon />Settings
        </DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  </div>

  {#if !rail.inCall}
    <ConnectionDot status={rail.connection} />
  {/if}

  {#if rail.inCall}
    <!-- Everything a live call adds to the header, grouped for styling (.sc-call in components.css). -->
    <div class="sc-call flex shrink-0 items-center gap-1" data-call-state={rail.callState} data-muted={rail.muted}>
      {#if label}
        <span
          class="sc-call-state inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground"
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
    </div>
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

</header>
