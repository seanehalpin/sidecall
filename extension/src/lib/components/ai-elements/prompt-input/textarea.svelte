<script lang="ts">
	import { cn } from "$lib/utils";
	import { Textarea } from "$lib/components/ui/textarea/index.js";
	import type { HTMLTextareaAttributes } from "svelte/elements";

	interface Props extends Omit<HTMLTextareaAttributes, "value"> {
		ref?: HTMLTextAreaElement | null;
		value?: string;
	}

	let { ref = $bindable(null), class: className, value = $bindable(""), onkeydown, ...props }: Props = $props();

	// Enter submits, Shift+Enter inserts a newline, and IME composition is left alone.
	function handleKeyDown(e: KeyboardEvent & { currentTarget: EventTarget & HTMLTextAreaElement }) {
		onkeydown?.(e);
		if (e.defaultPrevented) return;
		if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
			e.preventDefault();
			const form = e.currentTarget.form;
			const submit = form?.querySelector<HTMLButtonElement>('button[type="submit"]');
			if (submit?.disabled) return;
			form?.requestSubmit(submit ?? undefined);
		}
	}
</script>

<Textarea
	bind:ref
	bind:value
	class={cn(
		"w-full resize-none rounded-none border-none bg-transparent p-3 shadow-none ring-0 outline-none",
		"field-sizing-content max-h-40 min-h-10 focus-visible:ring-0 dark:bg-transparent",
		className
	)}
	onkeydown={handleKeyDown}
	{...props}
/>
