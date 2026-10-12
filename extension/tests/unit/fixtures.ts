import type { Approval, ServerMessage, ServerOf, ServerType, Task } from '../../src/lib/protocol';

let n = 0;

export function msg<T extends ServerType>(type: T, payload: ServerOf<T>['payload'], ts = 1000): ServerMessage {
  n += 1;
  return { type, id: `m${n}`, ts, payload } as ServerMessage;
}

export function task(id: string, patch: Partial<Task> = {}): Task {
  return {
    id,
    title: `Task ${id}`,
    brief: { title: `Task ${id}`, goal: 'g', targets: [], changes: [], acceptanceCriteria: [], outOfScope: [], notes: null },
    status: 'queued',
    createdAt: 1,
    startedAt: null,
    finishedAt: null,
    summary: null,
    files: [],
    error: null,
    lastEvent: null,
    page: null,
    request: null,
    pickIds: [],
    ...patch,
  };
}

export function approval(id: string, taskId: string): Approval {
  return { id, taskId, command: 'pnpm add left-pad', reason: 'Package install' };
}

export const repo = { name: 'svelte5-app', path: '/Users/me/fixtures/svelte5-app', origin: 'http://localhost:5173' };
