<script lang="ts">
  import type { ConnectionStatus } from '../rail-state';

  let { status }: { status: ConnectionStatus } = $props();

  const labels: Record<ConnectionStatus, string> = {
    connected: 'Connected',
    connecting: 'Connecting…',
    offline: 'Daemon offline',
  };
</script>

<span class="dot {status}" role="img" title={labels[status]} aria-label={labels[status]} data-status={status}></span>

<style>
  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
  .connected {
    background: var(--ok);
  }
  .connecting {
    background: #e0a800;
  }
  .offline {
    background: var(--fg-muted);
  }
</style>
