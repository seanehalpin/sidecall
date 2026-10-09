<script>
	let { items } = $props();
	let active = $state('Inbox');
</script>

<nav class="sidebar">
	<ul>
		{#each items as item (item)}
			<li>
				<a
					href="#{item.toLowerCase()}"
					class:active={active === item}
					data-testid="nav-item"
					onclick={() => (active = item)}>{item}</a
				>
			</li>
		{/each}
	</ul>
</nav>

<style>
	.sidebar { width: 200px; background: #f4f4f5; padding: 16px; }
	ul { list-style: none; margin: 0; padding: 0; }
	a { display: block; padding: 8px; color: #333; text-decoration: none; border-radius: 6px; }
	a.active { background: #e4e4e7; font-weight: 600; }
</style>
