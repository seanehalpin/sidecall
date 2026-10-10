// Inserting "[n]" into the composer text at the caret (pure, so it can be unit tested).

export interface InsertResult {
  text: string;
  /** Caret position after the inserted marker (and its trailing space). */
  caret: number;
}

/** Replace the selection with `insert`, padding with spaces so it doesn't glue onto words. */
export function insertAtRange(text: string, start: number, end: number, insert: string): InsertResult {
  const a = Math.max(0, Math.min(start, text.length));
  const b = Math.max(a, Math.min(end, text.length));
  const before = text.slice(0, a);
  const after = text.slice(b);
  const lead = before !== '' && !/\s$/.test(before) ? ' ' : '';
  const trail = after === '' || !/^[\s.,;:!?)]/.test(after) ? ' ' : '';
  const inserted = `${lead}${insert}${trail}`;
  return { text: before + inserted + after, caret: before.length + inserted.length };
}
