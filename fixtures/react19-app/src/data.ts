export const navItems = ['Inbox', 'Projects', 'Reports', 'Team', 'Settings'];

export type CardData = { id: number; title: string; body: string };

export const cards: CardData[] = Array.from({ length: 12 }, (_, i) => ({
  id: i + 1,
  title: `Project ${i + 1}`,
  body: `Short description for project ${i + 1}. Updated ${i + 1} days ago.`,
}));
