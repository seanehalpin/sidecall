// Messages between extension pages (rail, options) and the service worker.
// Pages connect with chrome.runtime.connect({ name }) and the worker pushes the
// full RailState on every change.
import type { PickCommand } from './picker-protocol';
import type { ClientDraft } from './protocol';
import type { RailState } from './rail-state';

/** The side panel. While one is connected, notify messages are not shown as notifications. */
export const RAIL_PORT = 'rail';
/** The options page: same state and commands, but doesn't count as an open rail. */
export const OPTIONS_PORT = 'options';

export const STORAGE_KEYS = {
  /** chrome.storage.local */
  port: 'daemonPort',
  token: 'token',
  /** chrome.storage.session */
  railState: 'railState',
  lastWindowId: 'lastWindowId',
  /** Last pick id handed out; survives a worker restart so ids are never reused. */
  pickCounter: 'pickCounter',
  /** Full Pick payloads + anchors of live picks, so they can be re-sent and re-pinned. */
  pickStore: 'pickStore',
} as const;

export const DEFAULT_DAEMON_PORT = 7823;

/**
 * Client message types a page may send. The worker owns hello, pair.request and pong,
 * and the call lifecycle (call.* and webrtc.*), which pages drive with call commands.
 */
export type PageDraft = Exclude<
  ClientDraft,
  { type: 'hello' | 'pair.request' | 'pong' | 'call.start' | 'call.end' | 'call.mute' | 'webrtc.offer' | 'webrtc.ice' }
>;

export type PageCommand =
  | { cmd: 'send'; draft: PageDraft }
  | { cmd: 'pair'; code: string }
  | { cmd: 'forgetPairing' }
  | { cmd: 'setPort'; port: number }
  | { cmd: 'reconnect' }
  | { cmd: 'clearError' }
  | { cmd: 'openOptions' }
  | { cmd: 'callStart' }
  | { cmd: 'callEnd' }
  | { cmd: 'callMute'; muted: boolean }
  | PickCommand;

export type WorkerMessage = { kind: 'state'; state: RailState; daemonPort: number };

const PAGE_DRAFT_TYPES: ReadonlySet<string> = new Set<PageDraft['type']>([
  'repo.map',
  'repo.browse',
  'pick.add',
  'pick.remove',
  'picks.clear',
  'user.text',
  'task.cancel',
  'task.reorder',
  'approval.respond',
  'settings.update',
  'chat.clear',
]);

export function isPageDraft(value: unknown): value is PageDraft {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return typeof v.type === 'string' && PAGE_DRAFT_TYPES.has(v.type) && typeof v.payload === 'object';
}

export function parsePort(value: unknown): number | null {
  const n = typeof value === 'string' ? Number(value.trim()) : value;
  if (typeof n !== 'number' || !Number.isInteger(n) || n < 1 || n > 65535) return null;
  return n;
}

/** Pairing codes are 6 digits (SPEC §9.1); tolerate spaces and dashes when pasted. */
export function normalisePairingCode(input: string): string | null {
  const digits = input.replace(/[\s-]/g, '');
  return /^\d{6}$/.test(digits) ? digits : null;
}
