// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { freshDebugStack, reactRepeatKey } from '../../src/lib/picker/frameworks/react';

const stackOf = (file: string, line: number, col: number) =>
  Object.assign(new Error('react-stack-top-frame'), {
    stack: `Error: react-stack-top-frame\n    at jsxDEV (http://x/node_modules/.vite/deps/jsx.js:1:1)\n    at Card (${file}:${line}:${col})`,
  });

// minimal fibers: only the fields the key logic reads
const fiber = (over: Record<string, unknown>) => ({ tag: 5, index: 0, key: null, type: 'h2', elementType: 'h2', ...over }) as never;

describe('repeat key (React)', () => {
  it('React 18: keys on _debugSource', () => {
    const f = fiber({ _debugSource: { fileName: '/a/Card.tsx', lineNumber: 10, columnNumber: 9 } });
    expect(reactRepeatKey(f)).toBe('src:/a/Card.tsx:10:9');
  });

  it('React 19: keys on the first user frame with the ?t= query stripped', () => {
    const a = fiber({ _debugStack: stackOf('http://x/src/Card.tsx?t=1', 3, 5) });
    const b = fiber({ _debugStack: stackOf('http://x/src/Card.tsx?t=2', 3, 5) });
    expect(reactRepeatKey(a)).toBe('stk:http://x/src/Card.tsx:3:5');
    expect(reactRepeatKey(a)).toBe(reactRepeatKey(b));
  });

  it('React 19: prefers the fresh element stack from a host parent after Fast Refresh', () => {
    const fresh = stackOf('http://x/src/Card.tsx?t=9', 4, 5);
    const parent = { tag: 5, memoizedProps: { children: [{ type: 'h2', key: null, _debugStack: fresh }] } };
    const f = fiber({ return: parent, _debugStack: stackOf('http://x/src/Card.tsx?t=1', 3, 5) });
    const r = freshDebugStack(f);
    expect(r.fresh).toBe(true);
    expect(r.stack).toBe(fresh);
    expect(reactRepeatKey(f)).toBe('stk:http://x/src/Card.tsx:4:5');
  });

  it('does not use a child at the same index when the type differs', () => {
    const parent = { tag: 5, memoizedProps: { children: [{ type: 'p', key: null, _debugStack: stackOf('http://x/a.tsx', 1, 1) }] } };
    const f = fiber({ return: parent, _debugStack: stackOf('http://x/src/Card.tsx', 3, 5) });
    expect(freshDebugStack(f).fresh).toBe(false);
  });

  it('falls back to type + owner', () => {
    const f = fiber({ type: 'li' });
    expect(reactRepeatKey(f)).toBe('own:li:');
  });
});
