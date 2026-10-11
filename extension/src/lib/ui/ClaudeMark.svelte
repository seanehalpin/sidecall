<script lang="ts">
  // The little pixel critter from Claude Code's terminal banner, drawn as an SVG grid.
  // `active` makes it bob while a task is being worked on.
  let { size = 20, active = false, class: className = '' }: { size?: number; active?: boolean; class?: string } = $props();

  // 11×7 grid: X = body, . = empty (the eyes are gaps in the body).
  const ROWS = [
    '..XXXXXXX..',
    '..X.XXX.X..',
    '..XXXXXXX..',
    'XXXXXXXXXXX',
    '..XXXXXXX..',
    '..X.X.X.X..',
    '..X.X.X.X..',
  ];
  const cells = ROWS.flatMap((row, y) => [...row].flatMap((c, x) => (c === 'X' ? [{ x, y }] : [])));
</script>

<svg
  class="claude-mark {className}"
  class:active
  width={size}
  height={(size * 7) / 11}
  viewBox="0 0 11 7"
  shape-rendering="crispEdges"
  aria-hidden="true"
>
  {#each cells as c (c.x + ':' + c.y)}
    <rect x={c.x} y={c.y} width="1" height="1" />
  {/each}
</svg>

<style>
  .claude-mark {
    flex: none;
    fill: var(--sc-claude-mark, #d97757);
  }
  .active {
    animation: bob 0.9s steps(2, jump-none) infinite;
  }
  @keyframes bob {
    from {
      transform: translateY(0);
    }
    to {
      transform: translateY(-1.5px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .active {
      animation: none;
    }
  }
</style>
