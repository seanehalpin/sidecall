<script lang="ts">
  // The ⋯ menu at the bottom-left of the message box: which repo this is, what it has cost
  // since the daemon started, clearing the chat, and settings.
  import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
  import SettingsIcon from '@lucide/svelte/icons/settings';
  import MessageSquareXIcon from '@lucide/svelte/icons/message-square-x';
  import * as DropdownMenu from '$lib/components/ui/dropdown-menu';
  import type { RailState } from '../rail-state';
  import type { WorkerModel } from '../protocol';
  import { costRows, formatUsd, totalCost } from '../costs';

  let {
    rail,
    onsettings,
    onclearchat,
    onclearchatdaily,
    onworkermodel,
  }: {
    rail: RailState;
    onsettings: () => void;
    onclearchat: () => void;
    onclearchatdaily: (on: boolean) => void;
    onworkermodel: (model: WorkerModel) => void;
  } = $props();

  const MODELS: { value: WorkerModel; label: string; hint: string }[] = [
    { value: 'sonnet', label: 'Sonnet', hint: 'Faster and cheaper; plenty for UI changes' },
    { value: 'opus', label: 'Opus', hint: 'For harder jobs; about 1.7x the cost' },
  ];
  const model = $derived(rail.settings?.workerModel ?? 'sonnet');

  const name = $derived(rail.repo ? rail.repo.name : 'No repo');
  const path = $derived(rail.repo ? rail.repo.path : (rail.origin ?? 'No local app'));
  const since = $derived(
    rail.costs ? new Date(rail.costs.since).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '',
  );
</script>

<DropdownMenu.Root>
  <DropdownMenu.Trigger
    class="sc-repo-trigger inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
    aria-label="Repo menu"
  >
    <EllipsisIcon class="size-4" />
  </DropdownMenu.Trigger>
  <DropdownMenu.Content align="start" side="top" class="sc-repo-menu w-64">
    <DropdownMenu.Label class="flex flex-col gap-0.5 font-normal">
      <span class="name text-sm font-medium text-foreground">{name}</span>
      <span class="path text-xs break-all text-muted-foreground">{path}</span>
    </DropdownMenu.Label>
    {#if rail.costs}
      <DropdownMenu.Separator />
      <DropdownMenu.Group class="sc-costs flex flex-col gap-1 px-2 py-1.5 text-xs" aria-label="Session cost">
        <div class="flex items-baseline justify-between gap-2">
          <span class="text-muted-foreground">Cost since {since}</span>
          <span class="total text-sm font-medium text-foreground tabular-nums">{formatUsd(totalCost(rail.costs))}</span>
        </div>
        {#each costRows(rail.costs) as row (row.label)}
          <div class="flex justify-between gap-2 text-muted-foreground">
            <span>{row.label}</span><span class="tabular-nums">{formatUsd(row.usd)}</span>
          </div>
        {/each}
      </DropdownMenu.Group>
    {/if}
    {#if rail.repo}
      <DropdownMenu.Separator />
      <!-- Inline, not a submenu: the side panel is too narrow for one to fly out. -->
      <DropdownMenu.Label class="sc-model px-2 pt-1.5 pb-0.5 text-xs font-normal text-muted-foreground">Claude Code model</DropdownMenu.Label>
      <DropdownMenu.RadioGroup value={model} onValueChange={(v) => onworkermodel(v as WorkerModel)}>
        {#each MODELS as m (m.value)}
          <DropdownMenu.RadioItem value={m.value} closeOnSelect={false}>
            <span class="flex flex-col">
              <span>{m.label}</span>
              <span class="text-xs text-muted-foreground">{m.hint}</span>
            </span>
          </DropdownMenu.RadioItem>
        {/each}
      </DropdownMenu.RadioGroup>
      <DropdownMenu.Separator />
      <!-- Clearing also starts the chat agent on a fresh session, which keeps its context small. -->
      <DropdownMenu.Item onSelect={onclearchat} disabled={rail.transcript.length === 0}>
        <MessageSquareXIcon />Clear chat
      </DropdownMenu.Item>
      <DropdownMenu.CheckboxItem
        checked={rail.settings?.clearChatDaily ?? true}
        onCheckedChange={(on) => onclearchatdaily(on)}
      >
        Clear chat daily
      </DropdownMenu.CheckboxItem>
    {/if}
    <DropdownMenu.Separator />
    <DropdownMenu.Item onSelect={onsettings}>
      <SettingsIcon />Settings
    </DropdownMenu.Item>
  </DropdownMenu.Content>
</DropdownMenu.Root>
