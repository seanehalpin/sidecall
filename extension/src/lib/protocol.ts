// Typed helpers over the generated protocol types (SPEC §9).
// The shapes themselves live in protocol.gen.ts, generated from the daemon's
// Pydantic models; never hand-write them here.
import type {
  CallStatePayload,
  ClientMessage,
  ServerMessage,
  Task,
  TaskEventPayload,
  TranscriptEntry,
} from './protocol.gen';

export type * from './protocol.gen';

// Literal unions the generator inlines rather than naming.
export type Role = TranscriptEntry['role'];
export type CallState = CallStatePayload['state'];
export type TaskStatus = Task['status'];
export type TaskEventKind = TaskEventPayload['kind'];

export type ClientType = ClientMessage['type'];
export type ClientOf<T extends ClientType> = Extract<ClientMessage, { type: T }>;
export type ClientPayload<T extends ClientType> = ClientOf<T>['payload'];

export type ServerType = ServerMessage['type'];
export type ServerOf<T extends ServerType> = Extract<ServerMessage, { type: T }>;

/** A client message before it gets an envelope id and timestamp. */
export type ClientDraft = {
  [T in ClientType]: { type: T; payload: ClientPayload<T> };
}[ClientType];

/** Every server message type, for runtime checks. Kept exhaustive by the type below. */
export const SERVER_TYPES = [
  'welcome',
  'pair.result',
  'repo.unknown',
  'call.state',
  'transcript.partial',
  'transcript.final',
  'agent.text.delta',
  'agent.text.done',
  'task.created',
  'task.event',
  'task.completed',
  'task.failed',
  'task.blocked',
  'approval.resolved',
  'queue.state',
  'notify',
  'webrtc.answer',
  'webrtc.ice',
  'error',
  'ping',
] as const satisfies readonly ServerType[];

// Compile-time check: fails if a server message type is missing from SERVER_TYPES.
type MissingServerTypes = Exclude<ServerType, (typeof SERVER_TYPES)[number]>;
const _exhaustive: MissingServerTypes extends never ? true : MissingServerTypes = true;
void _exhaustive;

const serverTypeSet: ReadonlySet<string> = new Set(SERVER_TYPES);

export function newMessageId(): string {
  return crypto.randomUUID().replace(/-/g, '').slice(0, 12);
}

/** Wrap a payload in the `{type, id, ts, payload}` envelope. */
export function envelope<T extends ClientType>(
  type: T,
  payload: ClientPayload<T>,
  now: number = Date.now(),
): ClientOf<T> {
  return { type, id: newMessageId(), ts: now, payload } as ClientOf<T>;
}

export function envelopeFromDraft(draft: ClientDraft, now: number = Date.now()): ClientMessage {
  return envelope(draft.type, draft.payload as never, now);
}

/**
 * Parse a raw frame from the daemon. Checks the envelope shape and that the type
 * is known; payload contents are trusted (the daemon validates with Pydantic).
 */
export function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  if (typeof v.type !== 'string' || !serverTypeSet.has(v.type)) return null;
  if (!v.payload || typeof v.payload !== 'object') return null;
  return value as ServerMessage;
}
