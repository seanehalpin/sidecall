<script>
	import Sidebar from './components/Sidebar.svelte';
	import CardList from './components/CardList.svelte';
	import Form from './components/Form.svelte';
	import { navItems, cards } from './data.js';
</script>

<div class="layout">
	<Sidebar items={navItems} />
	<main>
		<h1>Projects</h1>
		<CardList {cards} />
		<Form />
	</main>
</div>

<style>
	.layout { display: flex; font-family: system-ui, sans-serif; min-height: 100vh; }
	main { flex: 1; padding: 24px; }
</style>
