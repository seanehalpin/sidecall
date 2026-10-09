<script>
	import Card from './Card.svelte';
	let { cards } = $props();
</script>

<section class="card-list">
	{#each cards as card (card.id)}
		<Card title={card.title} body={card.body} />
	{/each}
</section>

<style>
	.card-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
</style>
