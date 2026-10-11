<script lang="ts">
  import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
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
  <section class="approvals flex flex-col gap-2 border-b bg-muted/40 p-3" aria-label="Approvals">
    {#each approvals as approval (approval.id)}
      <Confirmation.Root
        class="card border-amber-500/40 bg-amber-500/10 text-foreground"
        data-approval-id={approval.id}
        approval={{ id: approval.id }}
        state="approval-requested"
      >
        <Confirmation.Title class="flex items-center gap-1.5 text-xs font-semibold">
          <ShieldAlertIcon class="size-3.5 text-amber-600 dark:text-amber-400" />
          Needs your OK
        </Confirmation.Title>
        <Confirmation.Request>
          <code class="command block rounded-lg bg-background/70 px-2.5 py-2 font-mono text-xs break-all whitespace-pre-wrap">{approval.command}</code>
          <p class="reason mt-1.5 text-xs text-muted-foreground">{approval.reason}</p>
        </Confirmation.Request>
        <Confirmation.Actions>
          <Confirmation.Action
            variant="outline"
            disabled={disabled || responded.has(approval.id)}
            onclick={() => respond(approval.id, false)}>Deny</Confirmation.Action
          >
          <Confirmation.Action disabled={disabled || responded.has(approval.id)} onclick={() => respond(approval.id, true)}
            >Approve</Confirmation.Action
          >
        </Confirmation.Actions>
      </Confirmation.Root>
    {/each}
  </section>
{/if}
