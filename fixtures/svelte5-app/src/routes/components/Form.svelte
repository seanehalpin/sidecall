<script>
	let name = $state('');
	let role = $state('viewer');
	let submitted = $state('');
</script>

<form class="invite-form" onsubmit={(e) => { e.preventDefault(); submitted = `${name} (${role})`; }}>
	<h2>Invite someone</h2>
	<input placeholder="Name" bind:value={name} />
	<select bind:value={role}>
		<option value="viewer">Viewer</option>
		<option value="editor">Editor</option>
		<option value="admin">Admin</option>
	</select>
	<button type="submit" data-testid="submit">Send invite</button>
	{#if submitted}<p>Invited {submitted}</p>{/if}
</form>

<style>
	.invite-form { margin-top: 32px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
	h2 { width: 100%; font-size: 16px; }
</style>
