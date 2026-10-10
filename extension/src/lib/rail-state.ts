// Rail state: what the side panel renders. The service worker owns it, applies
// server messages and local connection events through these pure functions,
// persists it to chrome.storage.session and broadcasts it to open rails.
import type {
  Approval,
  CallState,
  RepoInfo,
  Role,
  ServerMessage,
  Settings,
  Task,
  TranscriptEntry,
} from './protocol';
import type { PickChip } from './picker-protocol';
import { chipTitle } from './picks/chip';

export type ConnectionStatus = 'offline' | 'connecting' | 'connected';

export interface TranscriptItem {
  turnId: string;
  role: Role;
  text: string;
  pickIds: number[];
  ts: number;
  taskId: string | null;
  /** True while an agent reply is still streaming (agent.text.delta). */
  streaming: boolean;
}

export interface RailError {
  code: string;
  message: string;
}

/** What an old [n] marker in the transcript shows once its pick is gone. */
export interface PickHistoryEntry {
  label: string;
  tooltip: string;
}

export const PICK_HISTORY_LIMIT = 200;

export interface RailState {
  connection: ConnectionStatus;
  /** A token is stored. */
  paired: boolean;
  /** Active tab origin, already filtered to localhost / 127.0.0.1. */
  origin: string | null;
  repo: RepoInfo | null;
  /** Set to the origin the daemon reported as unmapped (repo.unknown). */
  repoUnknown: string | null;
  /** The daemon replied no_origin to the last hello. */
  noOrigin: boolean;
  transcript: TranscriptItem[];
  /** Live speech transcript (Milestone 3). */
  partial: string | null;
  tasks: Task[];
  approvals: Approval[];
  /** Latest task.event text per task id. */
  lastTaskEvent: Record<string, string>;
  settings: Settings | null;
  callState: CallState;
  clockOffset: number | null;
  /** Live picks (this and earlier turns until consumed by a task or cleared), by id. */
  picks: PickChip[];
  /** Label and tooltip of every pick seen this session, so old transcript markers keep tooltips. */
  pickHistory: Record<number, PickHistoryEntry>;
  /** The repo origin the live picks belong to; a welcome for another repo drops them. */
  picksOrigin: string | null;
  lastError: RailError | null;
}

export const TRANSCRIPT_LIMIT = 200;

export function initialRailState(): RailState {
  return {
    connection: 'offline',
    paired: false,
    origin: null,
    repo: null,
    repoUnknown: null,
    noOrigin: false,
    transcript: [],
    partial: null,
    tasks: [],
    approvals: [],
    lastTaskEvent: {},
    settings: null,
    callState: 'idle',
    clockOffset: null,
    picks: [],
    pickHistory: {},
    picksOrigin: null,
    lastError: null,
  };
}

/** Local events from the service worker, as opposed to daemon messages. */
export type LocalEvent =
  | { kind: 'connection'; status: ConnectionStatus }
  | { kind: 'paired'; paired: boolean }
  | { kind: 'origin'; origin: string | null }
  | { kind: 'error'; error: RailError }
  | { kind: 'clearError' }
  | { kind: 'pickAdd'; chip: PickChip; origin: string | null }
  | { kind: 'pickPatch'; id: number; patch: Partial<Pick<PickChip, 'status' | 'stale'>> }
  | { kind: 'picksRemove'; ids: number[] }
  | { kind: 'picksClear' }
  | { kind: 'picksDim'; ids: number[]; dimmed: boolean };

const PER_REPO_RESET: Pick<
  RailState,
  'repo' | 'transcript' | 'partial' | 'tasks' | 'approvals' | 'lastTaskEvent' | 'settings'
> = {
  repo: null,
  transcript: [],
  partial: null,
  tasks: [],
  approvals: [],
  lastTaskEvent: {},
  settings: null,
};

function fromEntry(e: TranscriptEntry): TranscriptItem {
  return {
    turnId: e.turnId,
    role: e.role,
    text: e.text,
    pickIds: e.pickIds,
    ts: e.ts,
    taskId: e.taskId,
    streaming: false,
  };
}

function capTranscript(items: TranscriptItem[]): TranscriptItem[] {
  return items.length > TRANSCRIPT_LIMIT ? items.slice(items.length - TRANSCRIPT_LIMIT) : items;
}

function upsertTask(tasks: Task[], task: Task): Task[] {
  const i = tasks.findIndex((t) => t.id === task.id);
  if (i === -1) return [...tasks, task];
  const next = tasks.slice();
  next[i] = task;
  return next;
}

function patchTask(tasks: Task[], id: string, patch: Partial<Task>): Task[] {
  return tasks.map((t) => (t.id === id ? { ...t, ...patch } : t));
}

function eventsFromTasks(tasks: Task[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of tasks) if (t.lastEvent) out[t.id] = t.lastEvent;
  return out;
}

/** Apply one daemon message. Unknown or transport-only messages return the same state. */
export function applyServerMessage(state: RailState, msg: ServerMessage): RailState {
  switch (msg.type) {
    case 'welcome': {
      const { repo, state: rs, clockOffset } = msg.payload;
      const otherRepo = state.picksOrigin !== null && state.picksOrigin !== repo.origin;
      return {
        ...state,
        ...PER_REPO_RESET,
        ...(otherRepo ? { picks: [], pickHistory: {}, picksOrigin: null } : {}),
        repo,
        repoUnknown: null,
        noOrigin: false,
        clockOffset,
        transcript: capTranscript(rs.transcript.map(fromEntry)),
        tasks: rs.tasks,
        approvals: rs.approvals,
        lastTaskEvent: eventsFromTasks(rs.tasks),
        settings: rs.settings,
        lastError: null,
      };
    }
    case 'repo.unknown':
      return {
        ...state,
        ...PER_REPO_RESET,
        repoUnknown: msg.payload.origin,
        noOrigin: false,
        lastError: null,
      };
    case 'pair.result':
      return { ...state, paired: true, lastError: null };
    case 'error': {
      const { code, message } = msg.payload;
      if (code === 'no_origin') {
        return { ...state, ...PER_REPO_RESET, noOrigin: true, repoUnknown: null, lastError: null };
      }
      if (code === 'unauthorized') {
        return { ...state, ...PER_REPO_RESET, paired: false, repoUnknown: null, lastError: { code, message } };
      }
      return { ...state, lastError: { code, message } };
    }
    case 'call.state':
      return { ...state, callState: msg.payload.state };
    case 'transcript.partial':
      return { ...state, partial: msg.payload.text };
    case 'transcript.final': {
      const p = msg.payload;
      const item: TranscriptItem = {
        turnId: p.turnId,
        role: p.role,
        text: p.text,
        pickIds: p.pickIds,
        ts: msg.ts,
        taskId: p.taskId,
        streaming: false,
      };
      const i = state.transcript.findIndex((e) => e.turnId === p.turnId && e.role === p.role);
      let transcript: TranscriptItem[];
      if (i === -1) {
        transcript = capTranscript([...state.transcript, item]);
      } else {
        transcript = state.transcript.slice();
        transcript[i] = { ...item, ts: transcript[i]?.ts ?? item.ts };
      }
      return { ...state, transcript, partial: p.role === 'user' ? null : state.partial };
    }
    case 'agent.text.delta': {
      const { turnId, delta } = msg.payload;
      const i = state.transcript.findIndex((e) => e.turnId === turnId && e.role === 'agent');
      if (i === -1) {
        const item: TranscriptItem = {
          turnId,
          role: 'agent',
          text: delta,
          pickIds: [],
          ts: msg.ts,
          taskId: null,
          streaming: true,
        };
        return { ...state, transcript: capTranscript([...state.transcript, item]) };
      }
      const existing = state.transcript[i];
      // A delta after the final text (late or duplicate frame) must not corrupt it.
      if (!existing || !existing.streaming) return state;
      const transcript = state.transcript.slice();
      transcript[i] = { ...existing, text: existing.text + delta };
      return { ...state, transcript };
    }
    case 'agent.text.done': {
      const { turnId } = msg.payload;
      const i = state.transcript.findIndex((e) => e.turnId === turnId && e.role === 'agent');
      const existing = state.transcript[i];
      if (!existing || !existing.streaming) return state;
      const transcript = state.transcript.slice();
      transcript[i] = { ...existing, streaming: false };
      return { ...state, transcript };
    }
    case 'task.created': {
      // Picks the task consumed leave the live set (SPEC §6.9); history keeps their tooltips.
      const used = new Set(msg.payload.pickIds);
      const picks = state.picks.some((p) => used.has(p.id)) ? state.picks.filter((p) => !used.has(p.id)) : state.picks;
      return { ...state, picks, tasks: upsertTask(state.tasks, msg.payload) };
    }
    case 'queue.state': {
      const tasks = msg.payload.tasks;
      const lastTaskEvent: Record<string, string> = {};
      // The daemon's lastEvent wins; otherwise keep what task.event told us.
      for (const t of tasks) {
        const text = t.lastEvent ?? state.lastTaskEvent[t.id];
        if (text) lastTaskEvent[t.id] = text;
      }
      return { ...state, tasks, lastTaskEvent };
    }
    case 'task.event': {
      const { taskId, text } = msg.payload;
      return {
        ...state,
        tasks: patchTask(state.tasks, taskId, { lastEvent: text }),
        lastTaskEvent: { ...state.lastTaskEvent, [taskId]: text },
      };
    }
    case 'task.completed': {
      const { taskId, summary, files } = msg.payload;
      return {
        ...state,
        tasks: patchTask(state.tasks, taskId, { status: 'completed', summary, files }),
      };
    }
    case 'task.failed': {
      const { taskId, reason } = msg.payload;
      return { ...state, tasks: patchTask(state.tasks, taskId, { status: 'failed', error: reason }) };
    }
    case 'task.blocked': {
      const { taskId, approval } = msg.payload;
      const approvals = state.approvals.some((a) => a.id === approval.id)
        ? state.approvals
        : [...state.approvals, approval];
      return { ...state, approvals, tasks: patchTask(state.tasks, taskId, { status: 'blocked' }) };
    }
    case 'approval.resolved': {
      const approval = state.approvals.find((a) => a.id === msg.payload.id);
      const approvals = state.approvals.filter((a) => a.id !== msg.payload.id);
      // The task resumes; queue.state will confirm the exact status.
      const tasks =
        approval && !approvals.some((a) => a.taskId === approval.taskId)
          ? state.tasks.map((t) =>
              t.id === approval.taskId && t.status === 'blocked' ? { ...t, status: 'running' as const } : t,
            )
          : state.tasks;
      return { ...state, approvals, tasks };
    }
    case 'notify':
    case 'ping':
    case 'webrtc.answer':
    case 'webrtc.ice':
      return state;
  }
}

export function applyLocalEvent(state: RailState, ev: LocalEvent): RailState {
  switch (ev.kind) {
    case 'connection':
      if (ev.status === state.connection) return state;
      // Streaming bubbles can't finish once the socket drops; freeze them.
      return ev.status === 'connected'
        ? { ...state, connection: ev.status }
        : {
            ...state,
            connection: ev.status,
            transcript: state.transcript.some((e) => e.streaming)
              ? state.transcript.map((e) => (e.streaming ? { ...e, streaming: false } : e))
              : state.transcript,
          };
    case 'paired':
      return ev.paired
        ? { ...state, paired: true }
        : { ...state, ...PER_REPO_RESET, paired: false, repoUnknown: null, noOrigin: false };
    case 'origin':
      if (ev.origin === state.origin) return state;
      return { ...state, origin: ev.origin };
    case 'error':
      return { ...state, lastError: ev.error };
    case 'clearError':
      return state.lastError ? { ...state, lastError: null } : state;
    case 'pickAdd': {
      const picks = [...state.picks.filter((p) => p.id !== ev.chip.id), ev.chip].sort((a, b) => a.id - b.id);
      const pickHistory = { ...state.pickHistory, [ev.chip.id]: { label: chipTitle(ev.chip), tooltip: ev.chip.tooltip } };
      const ids = Object.keys(pickHistory).map(Number);
      if (ids.length > PICK_HISTORY_LIMIT) {
        ids.sort((a, b) => a - b);
        for (const old of ids.slice(0, ids.length - PICK_HISTORY_LIMIT)) delete pickHistory[old];
      }
      return { ...state, picks, pickHistory, picksOrigin: ev.origin ?? state.picksOrigin };
    }
    case 'pickPatch': {
      const i = state.picks.findIndex((p) => p.id === ev.id);
      const cur = state.picks[i];
      if (!cur) return state;
      if ((ev.patch.status === undefined || ev.patch.status === cur.status) && (ev.patch.stale === undefined || ev.patch.stale === cur.stale)) {
        return state;
      }
      const picks = state.picks.slice();
      picks[i] = { ...cur, ...ev.patch };
      return { ...state, picks };
    }
    case 'picksRemove': {
      const gone = new Set(ev.ids);
      if (!state.picks.some((p) => gone.has(p.id))) return state;
      return { ...state, picks: state.picks.filter((p) => !gone.has(p.id)) };
    }
    case 'picksClear':
      return state.picks.length ? { ...state, picks: [] } : state;
    case 'picksDim': {
      const which = new Set(ev.ids);
      if (!state.picks.some((p) => which.has(p.id) && p.dimmed !== ev.dimmed)) return state;
      return { ...state, picks: state.picks.map((p) => (which.has(p.id) ? { ...p, dimmed: ev.dimmed } : p)) };
    }
  }
}

/** Which screen the rail shows in its main area. */
export type RailView = 'offline' | 'pairing' | 'no_origin' | 'repo_unknown' | 'binding' | 'main';

export function railView(state: RailState): RailView {
  if (state.connection === 'offline') return 'offline';
  if (state.connection === 'connecting' && !state.repo) return 'offline';
  if (!state.paired) return 'pairing';
  if (state.noOrigin || state.origin === null) return 'no_origin';
  if (state.repoUnknown) return 'repo_unknown';
  if (state.repo) return 'main';
  return 'binding';
}

/** The composer and task actions only work when connected and bound to a repo. */
export function canSend(state: RailState): boolean {
  return state.connection === 'connected' && railView(state) === 'main';
}

export interface TaskGroups {
  running: Task[];
  queued: Task[];
  finished: Task[];
}

export function groupTasks(tasks: Task[]): TaskGroups {
  const running: Task[] = [];
  const queued: Task[] = [];
  const finished: Task[] = [];
  for (const t of tasks) {
    if (t.status === 'running' || t.status === 'blocked') running.push(t);
    else if (t.status === 'queued') queued.push(t);
    else finished.push(t);
  }
  finished.sort((a, b) => (b.finishedAt ?? b.createdAt) - (a.finishedAt ?? a.createdAt));
  return { running, queued, finished };
}

/** Move `id` to sit before `beforeId` (or to the end when null); returns queued ids. */
export function reorderIds(ids: string[], id: string, beforeId: string | null): string[] {
  if (id === beforeId) return ids;
  const without = ids.filter((x) => x !== id);
  if (without.length === ids.length) return ids;
  const at = beforeId === null ? -1 : without.indexOf(beforeId);
  if (at === -1) return [...without, id];
  return [...without.slice(0, at), id, ...without.slice(at)];
}
