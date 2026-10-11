<script lang="ts">
  import { Badge } from '$lib/components/ui/badge';
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

<Badge variant="outline" class="shrink-0 gap-1.5" role="img" aria-label={labels[status]} data-status={status} title={labels[status]}>
  <span
    class={cn(
      'size-1.5 rounded-full',
      status === 'connected' && 'bg-emerald-500',
      status === 'connecting' && 'animate-pulse bg-amber-500',
      status === 'offline' && 'bg-muted-foreground',
    )}
  ></span>
  {#if !compact}{shortLabels[status]}{/if}
</Badge>
