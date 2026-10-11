<script lang="ts">
  import { cn } from '$lib/utils';
  import type { ConnectionStatus } from '../rail-state';

  let { status, compact = false }: { status: ConnectionStatus; compact?: boolean } = $props();

  const labels: Record<ConnectionStatus, string> = {
    connected: 'Connected',
    connecting: 'Connecting…',
    offline: 'Daemon offline',
  };
  const shortLabels: Record<ConnectionStatus, string> = {
    connected: 'Connected',
    connecting: 'Connecting…',
    offline: 'Offline',
  };
</script>

<span class="inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground" role="img" aria-label={labels[status]} data-status={status} title={labels[status]}>
  <span
    class={cn(
      'size-1.5 rounded-full',
      status === 'connected' && 'bg-emerald-500',
      status === 'connecting' && 'animate-pulse bg-amber-500',
      status === 'offline' && 'bg-muted-foreground',
    )}
  ></span>
  <!-- Connected is the normal state: just the dot. Anything else says so. -->
  {#if !compact && status !== 'connected'}{shortLabels[status]}{/if}
</span>
