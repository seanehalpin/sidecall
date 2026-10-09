<script lang="ts">
  import type { RailState } from '../rail-state';
  import ConnectionDot from './ConnectionDot.svelte';

  let { rail, onsettings }: { rail: RailState; onsettings: () => void } = $props();
</script>

<header>
  <div class="repo">
    {#if rail.repo}
      <div class="name" title={rail.repo.name}>{rail.repo.name}</div>
      <div class="path muted" title={rail.repo.path}><span dir="ltr">{rail.repo.path}</span></div>
    {:else}
      <div class="name">Sidecall</div>
      <div class="path muted" title={rail.origin ?? ''}>{rail.origin ?? 'No local app'}</div>
    {/if}
  </div>
  <ConnectionDot status={rail.connection} />
  <button class="icon" disabled title="Voice arrives in Milestone 3" aria-label="Start call">
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z"
      />
    </svg>
  </button>
  <button class="icon" onclick={onsettings} title="Settings" aria-label="Settings">
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        fill="currentColor"
        d="M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.4-2.5 1a7.6 7.6 0 0 0-1.7-1L15 3.3h-4l-.4 2.7a7.6 7.6 0 0 0-1.7 1l-2.5-1-2 3.4L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.4 2.5-1a7.6 7.6 0 0 0 1.7 1l.4 2.7h4l.4-2.7a7.6 7.6 0 0 0 1.7-1l2.5 1 2-3.4zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z"
        transform="translate(-1 0)"
      />
    </svg>
  </button>
</header>

<style>
  header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }
  .repo {
    flex: 1;
    min-width: 0;
  }
  .name {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .path {
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    direction: rtl;
    text-align: left;
  }
  .icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 5px;
    border-color: transparent;
    background: transparent;
  }
  .icon:not(:disabled):hover {
    background: var(--bg-subtle);
  }
</style>
