import { describe, expect, it } from 'vitest';
import { backoffDelay, BACKOFF_MAX_MS } from '../../src/lib/backoff';
import { isPageDraft, normalisePairingCode, parsePort } from '../../src/lib/messaging';
import { allowedOrigin } from '../../src/lib/origin';
import { envelope, envelopeFromDraft, parseServerMessage } from '../../src/lib/protocol';

describe('allowedOrigin', () => {
  it.each([
    ['http://localhost:5173/some/route?x=1', 'http://localhost:5173'],
    ['http://localhost/', 'http://localhost'],
    ['http://127.0.0.1:3000/', 'http://127.0.0.1:3000'],
    ['http://LOCALHOST:8080/a', 'http://localhost:8080'],
  ])('%s → %s', (url, origin) => {
    expect(allowedOrigin(url)).toBe(origin);
  });

  it.each([
    'https://localhost:5173/',
    'http://example.com/',
    'http://localhost.evil.com/',
    'http://127.0.0.2:3000/',
    'http://[::1]:5173/',
    'http://user:pw@localhost:5173/',
    'chrome://extensions',
    'chrome-extension://kcjkimilmanlonlplfancfpoekkphjhb/sidepanel.html',
    'file:///Users/me/index.html',
    'about:blank',
    'data:text/html,hi',
    'not a url',
    '',
    undefined,
    null,
  ])('%s → null', (url) => {
    expect(allowedOrigin(url)).toBeNull();
  });
});

describe('backoffDelay', () => {
  it('starts at 1 s and doubles', () => {
    expect([0, 1, 2, 3, 4].map(backoffDelay)).toEqual([1000, 2000, 4000, 8000, 16000]);
  });

  it('caps at 30 s', () => {
    expect(backoffDelay(5)).toBe(BACKOFF_MAX_MS);
    expect(backoffDelay(1000)).toBe(BACKOFF_MAX_MS);
  });

  it('treats bad input as the first attempt', () => {
    expect(backoffDelay(-3)).toBe(1000);
    expect(backoffDelay(Number.NaN)).toBe(1000);
  });
});

describe('envelope', () => {
  it('adds a random id and the timestamp', () => {
    const a = envelope('user.text', { text: 'hi', pickIds: [], page: null }, 1234);
    const b = envelope('user.text', { text: 'hi', pickIds: [], page: null }, 1234);
    expect(a).toMatchObject({ type: 'user.text', ts: 1234, payload: { text: 'hi', pickIds: [] } });
    expect(a.id).toMatch(/^[0-9a-f]{12}$/);
    expect(a.id).not.toBe(b.id);
  });

  it('defaults ts to now', () => {
    const before = Date.now();
    const m = envelope('pong', { seq: 3 });
    expect(m.ts).toBeGreaterThanOrEqual(before);
  });

  it('builds from a draft', () => {
    expect(envelopeFromDraft({ type: 'task.reorder', payload: { ids: ['b', 'a'] } }, 1)).toMatchObject({
      type: 'task.reorder',
      ts: 1,
      payload: { ids: ['b', 'a'] },
    });
  });
});

describe('parseServerMessage', () => {
  it('accepts a known message', () => {
    const raw = JSON.stringify({ type: 'ping', id: 'x', ts: 1, payload: { seq: 4 } });
    expect(parseServerMessage(raw)).toEqual({ type: 'ping', id: 'x', ts: 1, payload: { seq: 4 } });
  });

  it.each([
    ['not json', '{'],
    ['unknown type', JSON.stringify({ type: 'nope', id: 'x', ts: 1, payload: {} })],
    ['missing payload', JSON.stringify({ type: 'ping', id: 'x', ts: 1 })],
    ['array', '[]'],
    ['binary', new ArrayBuffer(2)],
  ])('rejects %s', (_label, raw) => {
    expect(parseServerMessage(raw)).toBeNull();
  });
});

describe('page messaging helpers', () => {
  it('only lets pages send their own message types', () => {
    expect(isPageDraft({ type: 'user.text', payload: { text: 'x', pickIds: [] } })).toBe(true);
    expect(isPageDraft({ type: 'hello', payload: {} })).toBe(false);
    expect(isPageDraft({ type: 'pong', payload: { seq: 1 } })).toBe(false);
    expect(isPageDraft({ type: 'user.text' })).toBe(false);
    expect(isPageDraft(null)).toBe(false);
  });

  it('parses ports', () => {
    expect(parsePort('7823')).toBe(7823);
    expect(parsePort(8000)).toBe(8000);
    expect(parsePort('0')).toBeNull();
    expect(parsePort('70000')).toBeNull();
    expect(parsePort('12.5')).toBeNull();
    expect(parsePort('abc')).toBeNull();
  });

  it('normalises pairing codes', () => {
    expect(normalisePairingCode('123456')).toBe('123456');
    expect(normalisePairingCode(' 123 456 ')).toBe('123456');
    expect(normalisePairingCode('123-456')).toBe('123456');
    expect(normalisePairingCode('12345')).toBeNull();
    expect(normalisePairingCode('abcdef')).toBeNull();
  });
});
