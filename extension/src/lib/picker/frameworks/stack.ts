// V8 stack parsing for React 19's `_debugStack` (an Error created at the jsxDEV call site).

export interface Frame {
  fileName: string;
  lineNumber: number;
  columnNumber: number;
}

const FRAME = /^\s*at (?:.*? \()?(.+?):(\d+):(\d+)\)?\s*$/;

export function parseStack(stack: string): Frame[] {
  const out: Frame[] = [];
  for (const line of stack.split('\n')) {
    const m = FRAME.exec(line);
    if (m) out.push({ fileName: m[1]!, lineNumber: Number(m[2]), columnNumber: Number(m[3]) });
  }
  return out;
}

/** Frames inside React or pre-bundled dependencies, never the user's own JSX call site. */
export const isInternalFrame = (file: string): boolean =>
  /\/node_modules\//.test(file) || /\/\.vite\/deps\//.test(file) || /\/@(?:vite|react-refresh|fs\/.*node_modules)/.test(file);

export function firstUserFrame(stack: string | undefined | null): Frame | null {
  if (!stack) return null;
  for (const f of parseStack(stack)) if (!isInternalFrame(f.fileName)) return f;
  return null;
}

/** `?t=123` Vite HMR timestamp on a module URL (0 if absent). */
export function frameT(url: string): number {
  const m = /[?&]t=(\d+)/.exec(url);
  return m ? Number(m[1]) : 0;
}
