<script lang="ts" module>
	import { cn, type WithElementRef } from "$lib/utils";
	import type { HTMLAttributes } from "svelte/elements";
	import type { Snippet } from "svelte";
	import type { ToolUIPartApproval, ToolUIPartState } from "./confirmation-context.svelte.js";

	export interface ConfirmationProps extends WithElementRef<HTMLAttributes<HTMLDivElement>> {
		approval?: ToolUIPartApproval;
		state: ToolUIPartState;
		children?: Snippet;
	}
	// indexing
</script>

<script lang="ts">
	import { Alert } from "$lib/components/ui/alert/index.js";
	import { setConfirmationContext } from "./confirmation-context.svelte.js";

	let {
		class: className,
		approval,
		state,
		children,
		ref = $bindable(null),
		...restProps
	}: ConfirmationProps = $props();

	// Only render if approval exists and not in input states
	let shouldRender = $derived(
		approval && state !== "input-streaming" && state !== "input-available"
	);

	// The rail only ever shows pending requests, so the context is fixed at creation. (The upstream
	// `watch` that re-ran setContext after init throws outside component setup, so it was dropped.)
	// svelte-ignore state_referenced_locally
	setConfirmationContext({ approval, state });
</script>

{#if shouldRender}
	<Alert bind:ref class={cn("flex flex-col gap-2", className)} {...restProps}>
		{@render children?.()}
	</Alert>
{/if}
