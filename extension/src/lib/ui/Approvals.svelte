<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';
  import * as Confirmation from '$lib/components/ai-elements/confirmation';
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
  <section class="approvals flex flex-col gap-2" aria-label="Approvals">
    {#each approvals as approval (approval.id)}
      <Confirmation.Root class="card" data-approval-id={approval.id} approval={{ id: approval.id }} state="approval-requested">
        <Confirmation.Title>
          <Confirmation.Request>
            The worker wants to run <code class="command font-mono">{approval.command}</code>. <span class="reason"
              >{approval.reason}</span
            >
          </Confirmation.Request>
        </Confirmation.Title>
        <Confirmation.Actions>
          <Confirmation.Action variant="outline" disabled={disabled || responded.has(approval.id)} onclick={() => respond(approval.id, false)}
            >Deny</Confirmation.Action
          >
          <Confirmation.Action disabled={disabled || responded.has(approval.id)} onclick={() => respond(approval.id, true)}>Approve</Confirmation.Action>
        </Confirmation.Actions>
      </Confirmation.Root>
    {/each}
  </section>
{/if}
