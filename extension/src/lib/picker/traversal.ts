// ↑/↓ selection traversal (SPEC §5.2) as a pure path stack. The path remembers the element
// the pointer is actually over (chain[0]) and the ancestors we've climbed through, so ↓
// walks back down the original path rather than to an arbitrary child.

export interface PathStack<T> {
  chain: readonly T[];
  cursor: number;
}

export const startPath = <T>(origin: T): PathStack<T> => ({ chain: [origin], cursor: 0 });
export const currentOf = <T>(p: PathStack<T>): T => p.chain[p.cursor]!;
export const originOf = <T>(p: PathStack<T>): T => p.chain[0]!;

/** Move toward the parent. `parentOf` returns null at the top. */
export function moveUp<T>(p: PathStack<T>, parentOf: (t: T) => T | null): PathStack<T> {
  if (p.cursor < p.chain.length - 1) return { chain: p.chain, cursor: p.cursor + 1 };
  const parent = parentOf(p.chain[p.cursor]!);
  if (!parent) return p;
  return { chain: [...p.chain, parent], cursor: p.cursor + 1 };
}

/** Move back toward the original element. */
export function moveDown<T>(p: PathStack<T>): PathStack<T> {
  return p.cursor === 0 ? p : { chain: p.chain, cursor: p.cursor - 1 };
}
