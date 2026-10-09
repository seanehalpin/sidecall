<script>
	let { title, body } = $props();
	let starred = $state(false);
</script>

<article class="card">
	<header class="card-header">
		<h2 class="card-title" data-testid="card-title">{title}</h2>
	</header>
	<p class="card-body">{body}</p>
	<button type="button" onclick={() => (starred = !starred)}>
		{starred ? 'Starred' : 'Star'}
	</button>
</article>

<style>
	.card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; }
	.card-title { font-size: 16px; margin: 0 0 8px; }
	.card-body { color: #555; font-size: 14px; }
</style>
