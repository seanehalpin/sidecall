// Ring buffer of recent page errors (SPEC §5.3), installed at document_start so it is in
// place before any page script runs.

export class RingBuffer<T> {
  private items: T[] = [];
  constructor(private readonly capacity: number) {}
  push(item: T): void {
    this.items.push(item);
    if (this.items.length > this.capacity) this.items.shift();
  }
  toArray(): T[] {
    return this.items.slice();
  }
  get size(): number {
    return this.items.length;
  }
  clear(): void {
    this.items = [];
  }
}

const MAX_ENTRY = 500;

/** Render one console.error argument as text, without throwing or recursing. */
export function formatArg(a: unknown): string {
  try {
    if (typeof a === 'string') return a;
    if (a instanceof Error) return a.stack ? a.stack.split('\n').slice(0, 3).join('\n') : `${a.name}: ${a.message}`;
    if (a === undefined) return 'undefined';
    if (typeof a === 'function') return `[fn ${a.name || 'anonymous'}]`;
    if (a && typeof a === 'object') {
      const s = JSON.stringify(a);
      return s ?? String(a);
    }
    return String(a);
  } catch {
    return '[unserialisable]';
  }
}

export function formatConsoleError(args: unknown[]): string {
  const s = args.map(formatArg).join(' ');
  return s.length > MAX_ENTRY ? `${s.slice(0, MAX_ENTRY - 1)}…` : s;
}

export function formatErrorEvent(e: { message?: string; filename?: string; lineno?: number; error?: unknown }): string {
  const where = e.filename ? ` (${e.filename}:${e.lineno ?? 0})` : '';
  const msg = e.message || formatArg(e.error);
  return `${msg}${where}`.slice(0, MAX_ENTRY);
}

export function formatRejection(reason: unknown): string {
  return `Unhandled rejection: ${formatArg(reason)}`.slice(0, MAX_ENTRY);
}

export const errorBuffer = new RingBuffer<string>(20);

let installed = false;
export function installConsoleCapture(win: Window & typeof globalThis = window): void {
  if (installed) return;
  installed = true;
  const orig = win.console.error;
  win.console.error = function (this: unknown, ...args: unknown[]) {
    try {
      errorBuffer.push(formatConsoleError(args));
    } catch {
      /* never break the page's logging */
    }
    return orig.apply(this ?? win.console, args as []);
  };
  win.addEventListener('error', (e) => {
    try {
      // Resource load errors don't bubble, so anything seen here is a script error.
      errorBuffer.push(formatErrorEvent(e));
    } catch {
      /* ignore */
    }
  });
  win.addEventListener('unhandledrejection', (e) => {
    try {
      errorBuffer.push(formatRejection(e.reason));
    } catch {
      /* ignore */
    }
  });
}
