<script lang="ts">
	import { cn } from "$lib/utils";
	import type { Snippet } from "svelte";
	import type { HTMLFormAttributes } from "svelte/elements";

	interface Props extends Omit<HTMLFormAttributes, "onsubmit"> {
		class?: string;
		onSubmit: (event: SubmitEvent) => void;
		children?: Snippet;
	}

	let { class: className, onSubmit, children, ...props }: Props = $props();

	function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		onSubmit(event);
	}
</script>

<form
	class={cn("bg-background w-full overflow-hidden rounded-2xl border shadow-xs", className)}
	onsubmit={handleSubmit}
	{...props}
>
	{@render children?.()}
</form>
