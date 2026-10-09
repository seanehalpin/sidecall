export const navItems = ['Inbox', 'Projects', 'Reports', 'Team', 'Settings'];

export const cards = Array.from({ length: 12 }, (_, i) => ({
	id: i + 1,
	title: `Project ${i + 1}`,
	body: `Short description for project ${i + 1}. Updated ${i + 1} days ago.`
}));
