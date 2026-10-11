/** Follow the OS colour scheme by toggling the `dark` class that the shadcn tokens key off. */
export function initColorScheme(): void {
  const query = window.matchMedia('(prefers-color-scheme: dark)');
  const apply = () => document.documentElement.classList.toggle('dark', query.matches);
  apply();
  query.addEventListener('change', apply);
}
