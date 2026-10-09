<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';
  import type { Approval } from '../protocol';

  let {
    approvals,
    disabled,
    onrespond,
  }: {
    approvals: Approval[];
    disabled: boolean;
    onrespond: (id: string, allow: boolean) => void;
  } = $props();

  // Cards stay until approval.resolved arrives; this just prevents double clicks.
  const responded = new SvelteSet<string>();

  function respond(id: string, allow: boolean) {
    responded.add(id);
    onrespond(id, allow);
  }
</script>

{#if approvals.length > 0}
  <section class="approvals" aria-label="Approvals">
    {#each approvals as approval (approval.id)}
      <article class="card" data-approval-id={approval.id}>
        <div class="title">Needs your OK</div>
        <code>{approval.command}</code>
        <p class="reason">{approval.reason}</p>
        <div class="actions">
          <button
            class="primary"
            disabled={disabled || responded.has(approval.id)}
            onclick={() => respond(approval.id, true)}>Approve</button
          >
          <button disabled={disabled || responded.has(approval.id)} onclick={() => respond(approval.id, false)}
            >Deny</button
          >
        </div>
      </article>
    {/each}
  </section>
{/if}

<style>
  .approvals {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .card {
    border: 1px solid var(--warn-border);
    background: var(--warn-bg);
    border-radius: var(--radius);
    padding: 8px 10px;
  }
  .title {
    font-weight: 600;
    font-size: 12px;
    margin-bottom: 4px;
  }
  code {
    display: block;
    font-family: var(--mono);
    font-size: 12px;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .reason {
    margin: 4px 0 8px;
    color: var(--fg-muted);
  }
  .actions {
    display: flex;
    gap: 6px;
  }
</style>
