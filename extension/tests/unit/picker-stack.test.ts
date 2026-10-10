import { describe, expect, it } from 'vitest';
import { firstUserFrame, frameT, isInternalFrame, parseStack } from '../../src/lib/picker/frameworks/stack';

const STACK = `Error: react-stack-top-frame
    at jsxDEV (http://localhost:5275/node_modules/.vite/deps/react_jsx-dev-runtime.js?v=abc:100:10)
    at Card (http://localhost:5275/src/components/Card.tsx?t=1700:10:8)
    at Object.react_stack_bottom_frame (http://localhost:5275/node_modules/.vite/deps/react-dom_client.js:1:1)
    at http://localhost:5275/src/App.tsx:12:3`;

describe('React 19 _debugStack parsing', () => {
  it('parses V8 frames with and without function names', () => {
    const f = parseStack(STACK);
    expect(f).toHaveLength(4);
    expect(f[1]).toEqual({ fileName: 'http://localhost:5275/src/components/Card.tsx?t=1700', lineNumber: 10, columnNumber: 8 });
    expect(f[3]!.fileName).toBe('http://localhost:5275/src/App.tsx');
  });

  it('picks the first non-library frame (the JSX call site)', () => {
    expect(firstUserFrame(STACK)?.lineNumber).toBe(10);
    expect(firstUserFrame('Error: x')).toBeNull();
    expect(firstUserFrame(undefined)).toBeNull();
  });

  it('knows library frames', () => {
    expect(isInternalFrame('http://x/node_modules/.vite/deps/react.js')).toBe(true);
    expect(isInternalFrame('http://x/src/Card.tsx')).toBe(false);
  });

  it('reads the HMR timestamp', () => {
    expect(frameT('http://x/src/Card.tsx?t=1700&v=2')).toBe(1700);
    expect(frameT('http://x/src/Card.tsx')).toBe(0);
  });
});
