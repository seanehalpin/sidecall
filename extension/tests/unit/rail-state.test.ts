import { describe, expect, it } from 'vitest';
import type { PickChip } from '../../src/lib/picker-protocol';
import {
  applyLocalEvent,
  applyServerMessage,
  callLabel,
  canSend,
  groupTasks,
  initialRailState,
  railView,
  reorderIds,
  TASK_STEPS_LIMIT,
  TRANSCRIPT_LIMIT,
  type RailState,
} from '../../src/lib/rail-state';
import { NO_COSTS, approval, msg, repo, task } from './fixtures';

const apply = (s: RailState, ...msgs: Parameters<typeof applyServerMessage>[1][]) =>
  msgs.reduce(applyServerMessage, s);

function bound(): RailState {
  let s = initialRailState();
  s = applyLocalEvent(s, { kind: 'connection', status: 'connected' });
  s = applyLocalEvent(s, { kind: 'paired', paired: true });
  s = applyLocalEvent(s, { kind: 'origin', origin: repo.origin });
  return apply(
    s,
    msg('welcome', {
      repo,
      clockOffset: 12,
      state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS },
    }),
  );
}

describe('welcome', () => {
  it('binds the repo and loads its state', () => {
    let s = initialRailState();
    s = applyLocalEvent(s, { kind: 'paired', paired: true });
    s = apply(
      s,
      msg('welcome', {
        repo,
        clockOffset: -5,
        state: {
          tasks: [task('t1', { status: 'running', lastEvent: 'editing src/App.svelte' })],
          approvals: [approval('a1', 't1')],
          transcript: [{ turnId: 'u1', role: 'user', text: 'hi', pickIds: [], ts: 5, taskId: null }],
          settings: { preset: 'cautious', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS,
        },
      }),
    );
    expect(s.repo).toEqual(repo);
    expect(s.clockOffset).toBe(-5);
    expect(s.tasks).toHaveLength(1);
    expect(s.approvals).toHaveLength(1);
    expect(s.transcript).toEqual([
      { turnId: 'u1', role: 'user', text: 'hi', pickIds: [], ts: 5, taskId: null, streaming: false },
    ]);
    expect(s.lastTaskEvent).toEqual({ t1: 'editing src/App.svelte' });
    expect(s.settings).toEqual({ preset: 'cautious', clearChatDaily: true, workerModel: 'sonnet' });
  });

  it('replaces per-repo state from a previous binding', () => {
    let s = bound();
    s = apply(
      s,
      msg('transcript.final', { turnId: 'u1', role: 'user', text: 'old', pickIds: [], taskId: null }),
      msg('task.created', task('old')),
      msg('task.blocked', { taskId: 'old', approval: approval('a-old', 'old') }),
      msg('task.event', { taskId: 'old', kind: 'status', text: 'x' }),
      msg('error', { code: 'bad_path', message: 'nope' }),
    );
    const other = { name: 'react19-app', path: '/r', origin: 'http://localhost:5174' };
    s = apply(
      s,
      msg('welcome', {
        repo: other,
        clockOffset: 0,
        state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS },
      }),
    );
    expect(s.repo).toEqual(other);
    expect(s.transcript).toEqual([]);
    expect(s.tasks).toEqual([]);
    expect(s.approvals).toEqual([]);
    expect(s.lastTaskEvent).toEqual({});
    expect(s.lastError).toBeNull();
    expect(s.repoUnknown).toBeNull();
  });

  it('clears repo.unknown and no_origin', () => {
    let s = apply(bound(), msg('repo.unknown', { origin: 'http://localhost:9999' }));
    expect(s.repoUnknown).toBe('http://localhost:9999');
    s = apply(
      s,
      msg('welcome', { repo, clockOffset: 0, state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS } }),
    );
    expect(s.repoUnknown).toBeNull();
    expect(railView(s)).toBe('main');
  });
});

describe('repo binding', () => {
  it('repo.unknown drops the old repo and shows setup', () => {
    let s = apply(bound(), msg('task.created', task('t1')));
    s = apply(s, msg('repo.unknown', { origin: 'http://localhost:3000' }));
    expect(s.repo).toBeNull();
    expect(s.tasks).toEqual([]);
    expect(s.repoUnknown).toBe('http://localhost:3000');
    expect(railView(s)).toBe('repo_unknown');
    expect(canSend(s)).toBe(false);
  });

  it('no_origin error shows the no-localhost screen', () => {
    const s = apply(bound(), msg('error', { code: 'no_origin', message: 'No origin' }));
    expect(s.noOrigin).toBe(true);
    expect(s.repo).toBeNull();
    expect(s.lastError).toBeNull();
    expect(railView(s)).toBe('no_origin');
  });

  it('a null local origin shows the no-localhost screen', () => {
    const s = applyLocalEvent(bound(), { kind: 'origin', origin: null });
    expect(railView(s)).toBe('no_origin');
  });

  it('bad_path keeps repo.unknown and records the error', () => {
    let s = apply(bound(), msg('repo.unknown', { origin: 'http://localhost:3000' }));
    s = apply(s, msg('error', { code: 'bad_path', message: 'Not a directory' }));
    expect(s.repoUnknown).toBe('http://localhost:3000');
    expect(s.lastError).toEqual({ code: 'bad_path', message: 'Not a directory' });
  });
});

describe('pairing and auth', () => {
  it('pair.result marks the rail as paired', () => {
    let s = applyLocalEvent(initialRailState(), { kind: 'connection', status: 'connected' });
    s = applyLocalEvent(s, { kind: 'origin', origin: repo.origin });
    expect(railView(s)).toBe('pairing');
    s = apply(s, msg('error', { code: 'bad_pairing_code', message: 'Wrong code' }));
    expect(s.lastError?.code).toBe('bad_pairing_code');
    s = apply(s, msg('pair.result', { token: 'tok' }));
    expect(s.paired).toBe(true);
    expect(s.lastError).toBeNull();
    expect(railView(s)).toBe('binding');
  });

  it('unauthorized unpairs and drops the repo', () => {
    const s = apply(bound(), msg('error', { code: 'unauthorized', message: 'Bad token' }));
    expect(s.paired).toBe(false);
    expect(s.repo).toBeNull();
    expect(s.lastError?.code).toBe('unauthorized');
    expect(railView(s)).toBe('pairing');
  });
});

describe('transcript', () => {
  it('streams agent deltas into one bubble, then finalises it', () => {
    let s = apply(
      bound(),
      msg('transcript.final', { turnId: 'u1', role: 'user', text: 'make it red', pickIds: [], taskId: null }),
      msg('agent.text.delta', { turnId: 'a1', delta: 'Sure, ' }),
      msg('agent.text.delta', { turnId: 'a1', delta: 'making the ' }),
    );
    expect(s.transcript).toHaveLength(2);
    expect(s.transcript[1]).toMatchObject({ role: 'agent', text: 'Sure, making the ', streaming: true });

    s = apply(s, msg('agent.text.delta', { turnId: 'a1', delta: 'heading red.' }), msg('agent.text.done', { turnId: 'a1' }));
    expect(s.transcript[1]).toMatchObject({ text: 'Sure, making the heading red.', streaming: false });

    s = apply(
      s,
      msg('transcript.final', { turnId: 'a1', role: 'agent', text: 'Sure, making the heading red!', pickIds: [], taskId: null }),
    );
    expect(s.transcript).toHaveLength(2);
    expect(s.transcript[1]).toMatchObject({ text: 'Sure, making the heading red!', streaming: false });
  });

  it('ignores deltas that arrive after the final text', () => {
    let s = apply(
      bound(),
      msg('transcript.final', { turnId: 'a1', role: 'agent', text: 'Done.', pickIds: [], taskId: null }),
      msg('agent.text.delta', { turnId: 'a1', delta: ' extra' }),
    );
    expect(s.transcript[0]?.text).toBe('Done.');
    s = apply(s, msg('agent.text.done', { turnId: 'a1' }));
    expect(s.transcript[0]?.streaming).toBe(false);
  });

  it('keeps interleaved turns separate', () => {
    const s = apply(
      bound(),
      msg('agent.text.delta', { turnId: 'a1', delta: 'one' }),
      msg('transcript.final', { turnId: 's1', role: 'system', text: 'Task queued: Red heading', pickIds: [], taskId: 't1' }),
      msg('agent.text.delta', { turnId: 'a1', delta: ' two' }),
    );
    expect(s.transcript.map((e) => [e.role, e.text])).toEqual([
      ['agent', 'one two'],
      ['system', 'Task queued: Red heading'],
    ]);
    expect(s.transcript[1]?.taskId).toBe('t1');
  });

  it('caps the transcript length', () => {
    let s = bound();
    for (let i = 0; i < TRANSCRIPT_LIMIT + 10; i++) {
      s = apply(s, msg('transcript.final', { turnId: `u${i}`, role: 'user', text: `${i}`, pickIds: [], taskId: null }));
    }
    expect(s.transcript).toHaveLength(TRANSCRIPT_LIMIT);
    expect(s.transcript[0]?.text).toBe('10');
  });

  it('freezes a streaming bubble when the socket drops', () => {
    let s = apply(bound(), msg('agent.text.delta', { turnId: 'a1', delta: 'half' }));
    s = applyLocalEvent(s, { kind: 'connection', status: 'offline' });
    expect(s.transcript[0]?.streaming).toBe(false);
  });

  it('a user final clears the live partial', () => {
    let s = apply(bound(), msg('transcript.partial', { text: 'make the' }));
    expect(s.partial).toBe('make the');
    s = apply(s, msg('transcript.final', { turnId: 'u1', role: 'user', text: 'make the heading red', pickIds: [], taskId: null }));
    expect(s.partial).toBeNull();
  });
});

describe('tasks', () => {
  it('task.created, task.event, task.completed', () => {
    let s = apply(bound(), msg('task.created', task('t1')), msg('task.created', task('t1', { title: 'Renamed' })));
    expect(s.tasks).toHaveLength(1);
    expect(s.tasks[0]?.title).toBe('Renamed');
    s = apply(s, msg('task.event', { taskId: 't1', kind: 'edit', text: 'editing src/routes/+page.svelte' }));
    expect(s.lastTaskEvent.t1).toBe('editing src/routes/+page.svelte');
    s = apply(s, msg('task.completed', { taskId: 't1', summary: 'Heading is red', files: ['src/routes/+page.svelte'] }));
    expect(s.tasks[0]).toMatchObject({ status: 'completed', summary: 'Heading is red', files: ['src/routes/+page.svelte'] });
  });

  it('task.failed records the reason', () => {
    const s = apply(bound(), msg('task.created', task('t1', { status: 'running' })), msg('task.failed', { taskId: 't1', reason: 'boom' }));
    expect(s.tasks[0]).toMatchObject({ status: 'failed', error: 'boom' });
  });

  it('queue.state replaces the task list and keeps known events', () => {
    let s = apply(
      bound(),
      msg('task.created', task('t1', { status: 'running' })),
      msg('task.created', task('gone')),
      msg('task.event', { taskId: 't1', kind: 'tool', text: 'reading files' }),
      msg('task.event', { taskId: 'gone', kind: 'tool', text: 'x' }),
    );
    s = apply(s, msg('queue.state', { tasks: [task('t1', { status: 'running' }), task('t2'), task('t3', { lastEvent: 'from daemon' })] }));
    expect(s.tasks.map((t) => t.id)).toEqual(['t1', 't2', 't3']);
    expect(s.lastTaskEvent).toEqual({ t1: 'reading files', t3: 'from daemon' });
    expect(s.taskSteps).toEqual({ t1: [{ kind: 'tool', text: 'reading files' }], t3: [{ kind: 'status', text: 'from daemon' }] });
  });

  it('task.event keeps a capped step history without consecutive duplicates', () => {
    let s = apply(bound(), msg('task.created', task('t1', { status: 'running' })));
    s = apply(
      s,
      msg('task.event', { taskId: 't1', kind: 'status', text: 'reading files' }),
      msg('task.event', { taskId: 't1', kind: 'tool', text: 'reading files' }),
      msg('task.event', { taskId: 't1', kind: 'edit', text: 'editing a.svelte' }),
    );
    expect(s.taskSteps.t1).toEqual([
      { kind: 'tool', text: 'reading files' },
      { kind: 'edit', text: 'editing a.svelte' },
    ]);
    for (let i = 0; i < TASK_STEPS_LIMIT + 5; i++) s = apply(s, msg('task.event', { taskId: 't1', kind: 'text', text: `step ${i}` }));
    expect(s.taskSteps.t1).toHaveLength(TASK_STEPS_LIMIT);
    expect(s.taskSteps.t1?.at(-1)?.text).toBe(`step ${TASK_STEPS_LIMIT + 4}`);
  });
});

describe('approvals', () => {
  it('task.blocked adds a card once; approval.resolved removes it', () => {
    let s = apply(
      bound(),
      msg('task.created', task('t1', { status: 'running' })),
      msg('task.blocked', { taskId: 't1', approval: approval('a1', 't1') }),
      msg('task.blocked', { taskId: 't1', approval: approval('a1', 't1') }),
    );
    expect(s.approvals).toHaveLength(1);
    expect(s.tasks[0]?.status).toBe('blocked');
    s = apply(s, msg('approval.resolved', { id: 'a1', allow: true }));
    expect(s.approvals).toEqual([]);
    expect(s.tasks[0]?.status).toBe('running');
  });

  it('resolving an unknown approval is a no-op for tasks', () => {
    const s0 = apply(bound(), msg('task.created', task('t1', { status: 'blocked' })));
    const s = apply(s0, msg('approval.resolved', { id: 'zzz', allow: false }));
    expect(s.tasks[0]?.status).toBe('blocked');
  });
});

describe('transport messages', () => {
  it('ping and notify do not change state', () => {
    const s = bound();
    expect(applyServerMessage(s, msg('ping', { seq: 1 }))).toBe(s);
    expect(applyServerMessage(s, msg('notify', { title: 't', body: 'b', action: 'open' }))).toBe(s);
  });

  it('call.state is tracked during a call', () => {
    const s = applyLocalEvent(bound(), { kind: 'callStarting' });
    expect(apply(s, msg('call.state', { state: 'listening' })).callState).toBe('listening');
  });

  it('call.state without a call of ours stays idle', () => {
    expect(apply(bound(), msg('call.state', { state: 'listening' })).callState).toBe('idle');
  });
});

describe('call lifecycle', () => {
  it('starting, muting, ending', () => {
    let s = applyLocalEvent(bound(), { kind: 'callEnded', error: { code: 'x', message: 'old' } });
    s = applyLocalEvent(s, { kind: 'callStarting' });
    expect(s).toMatchObject({ inCall: true, callState: 'connecting', muted: false, callError: null });
    expect(callLabel(s)).toBe('Connecting…');
    s = apply(s, msg('call.state', { state: 'listening' }), msg('transcript.partial', { text: 'make it' }));
    expect(callLabel(s)).toBe('Listening');
    expect(s.partial).toBe('make it');
    s = applyLocalEvent(s, { kind: 'callMuted', muted: true });
    expect(s.muted).toBe(true);
    expect(callLabel(s)).toBe('Muted');
    expect(callLabel(apply(s, msg('call.state', { state: 'speaking' })))).toBe('Speaking');
    s = applyLocalEvent(s, { kind: 'callEnded' });
    expect(s).toMatchObject({ inCall: false, callState: 'idle', muted: false, partial: null, callError: null });
    expect(callLabel(s)).toBeNull();
  });

  it('mute is ignored outside a call; ending twice is a no-op', () => {
    const s = bound();
    expect(applyLocalEvent(s, { kind: 'callMuted', muted: true })).toBe(s);
    expect(applyLocalEvent(s, { kind: 'callEnded' })).toBe(s);
  });

  it('an abnormal end keeps the error until cleared', () => {
    let s = applyLocalEvent(bound(), { kind: 'callStarting' });
    s = applyLocalEvent(s, { kind: 'callEnded', error: { code: 'mic_permission', message: 'Grant mic' } });
    expect(s.callError?.code).toBe('mic_permission');
    s = applyLocalEvent(s, { kind: 'clearCallError' });
    expect(s.callError).toBeNull();
  });

  it('a rebind (origin change) keeps the call', () => {
    let s = applyLocalEvent(bound(), { kind: 'callStarting' });
    s = apply(s, msg('call.state', { state: 'listening' }));
    s = apply(s, msg('welcome', { repo, clockOffset: 0, state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS } }));
    expect(s).toMatchObject({ inCall: true, callState: 'listening' });
  });
});

describe('railView and canSend', () => {
  it('offline wins over everything', () => {
    const s = applyLocalEvent(bound(), { kind: 'connection', status: 'offline' });
    expect(railView(s)).toBe('offline');
    expect(canSend(s)).toBe(false);
  });

  it('reconnecting with a known repo keeps the main view but disables sending', () => {
    const s = applyLocalEvent(bound(), { kind: 'connection', status: 'connecting' });
    expect(railView(s)).toBe('main');
    expect(canSend(s)).toBe(false);
  });

  it('connecting without a repo shows the offline screen', () => {
    expect(railView(applyLocalEvent(initialRailState(), { kind: 'connection', status: 'connecting' }))).toBe('offline');
  });

  it('bound and connected can send', () => {
    expect(railView(bound())).toBe('main');
    expect(canSend(bound())).toBe(true);
  });

  it('unpairing locally drops the repo', () => {
    const s = applyLocalEvent(bound(), { kind: 'paired', paired: false });
    expect(s.repo).toBeNull();
    expect(railView(s)).toBe('pairing');
  });
});

describe('groupTasks / reorderIds', () => {
  it('groups by status and sorts finished newest first', () => {
    const g = groupTasks([
      task('q1'),
      task('r', { status: 'running' }),
      task('c1', { status: 'completed', finishedAt: 10 }),
      task('q2'),
      task('f', { status: 'failed', finishedAt: 20 }),
      task('x', { status: 'cancelled', finishedAt: 5 }),
    ]);
    expect(g.running.map((t) => t.id)).toEqual(['r']);
    expect(g.queued.map((t) => t.id)).toEqual(['q1', 'q2']);
    expect(g.finished.map((t) => t.id)).toEqual(['f', 'c1', 'x']);
  });

  it('blocked counts as running', () => {
    expect(groupTasks([task('b', { status: 'blocked' })]).running).toHaveLength(1);
  });

  it('moves an id before another or to the end', () => {
    expect(reorderIds(['a', 'b', 'c'], 'c', 'a')).toEqual(['c', 'a', 'b']);
    expect(reorderIds(['a', 'b', 'c'], 'a', 'c')).toEqual(['b', 'a', 'c']);
    expect(reorderIds(['a', 'b', 'c'], 'a', null)).toEqual(['b', 'c', 'a']);
    expect(reorderIds(['a', 'b', 'c'], 'b', 'b')).toEqual(['a', 'b', 'c']);
    expect(reorderIds(['a', 'b'], 'zz', 'a')).toEqual(['a', 'b']);
  });
});

describe('daemon error codes', () => {
  it.each(['bad_message', 'no_repo', 'bad_origin', 'unknown_task', 'unknown_approval', 'not_implemented', 'something_new'])(
    '%s is recorded without touching the binding',
    (code) => {
      const before = apply(bound(), msg('task.created', task('t1')));
      const s = apply(before, msg('error', { code, message: `msg for ${code}` }));
      expect(s.lastError).toEqual({ code, message: `msg for ${code}` });
      expect(s.repo).toEqual(before.repo);
      expect(s.tasks).toEqual(before.tasks);
      expect(railView(s)).toBe('main');
      expect(applyLocalEvent(s, { kind: 'clearError' }).lastError).toBeNull();
    },
  );
});

describe('picks in rail state', () => {
  const chip = (id: number, patch: Partial<PickChip> = {}): PickChip => ({
    id,
    kind: 'element',
    label: 'div.x',
    tooltip: 'src/a.svelte:1:1 (element)',
    component: null,
    path: 'div.x',
    source: null,
    repeat: null,
    stale: false,
    dimmed: false,
    status: 'capturing',
    ...patch,
  });
  const withPicks = () =>
    [1, 2, 3].reduce((s, id) => applyLocalEvent(s, { kind: 'pickAdd', chip: chip(id), origin: 'http://localhost:5173' }), initialRailState());

  it('adds chips sorted, records history and origin', () => {
    const s = applyLocalEvent(withPicks(), { kind: 'pickAdd', chip: chip(0 + 2, { label: 'h3' }), origin: null });
    expect(s.picks.map((p) => p.id)).toEqual([1, 2, 3]);
    expect(s.picks[1]?.label).toBe('h3');
    expect(s.pickHistory[1]).toEqual({ label: 'div.x', tooltip: 'src/a.svelte:1:1 (element)', path: 'div.x' });
    expect(s.picksOrigin).toBe('http://localhost:5173');
  });
  it('patches status and stale, no-op when unchanged', () => {
    const s = withPicks();
    const r = applyLocalEvent(s, { kind: 'pickPatch', id: 1, patch: { status: 'ready' } });
    expect(r.picks[0]?.status).toBe('ready');
    expect(applyLocalEvent(r, { kind: 'pickPatch', id: 1, patch: { status: 'ready' } })).toBe(r);
    expect(applyLocalEvent(r, { kind: 'pickPatch', id: 9, patch: { stale: true } })).toBe(r);
    expect(applyLocalEvent(r, { kind: 'pickPatch', id: 2, patch: { stale: true } }).picks[1]?.stale).toBe(true);
  });
  it('dims, removes (keeping history) and clears', () => {
    let s = applyLocalEvent(withPicks(), { kind: 'picksDim', ids: [1, 2], dimmed: true });
    expect(s.picks.map((p) => p.dimmed)).toEqual([true, true, false]);
    s = applyLocalEvent(s, { kind: 'picksRemove', ids: [2] });
    expect(s.picks.map((p) => p.id)).toEqual([1, 3]);
    expect(s.pickHistory[2]).toBeDefined();
    expect(applyLocalEvent(applyLocalEvent(s, { kind: 'picksClear' }), { kind: 'picksClear' }).picks).toEqual([]);
  });
  it('task.created with pickIds consumes those picks', () => {
    const s = applyServerMessage(withPicks(), msg('task.created', task('t1', { pickIds: [1, 3] })));
    expect(s.picks.map((p) => p.id)).toEqual([2]);
    expect(s.pickHistory[1]).toBeDefined();
  });
  it('keeps picks across a welcome for the same repo, drops them for another', () => {
    const w = (origin: string) =>
      msg('welcome', { repo: { ...repo, origin }, clockOffset: 42, state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS } });
    const same = applyServerMessage(withPicks(), w('http://localhost:5173'));
    expect(same.picks).toHaveLength(3);
    expect(same.clockOffset).toBe(42);
    const other = applyServerMessage(withPicks(), w('http://localhost:3000'));
    expect(other.picks).toEqual([]);
    expect(other.pickHistory).toEqual({});
  });
});

describe('user messages keep the picks they carried', () => {
  it('snapshots pick labels so reused numbers do not rename old messages', () => {
    let s = applyLocalEvent(initialRailState(), {
      kind: 'pickAdd',
      chip: { id: 1, kind: 'element', label: 'h2', tooltip: 'Card.svelte:8', component: 'Card', path: 'CardList › Card › h2', source: null, repeat: null, stale: false, dimmed: false, status: 'ready' },
      origin: 'http://localhost:5173',
    });
    s = applyServerMessage(s, {
      type: 'transcript.final', id: 'm', ts: 1,
      payload: { turnId: 'u1', role: 'user', text: 'make this green', pickIds: [1], taskId: null },
    } as never);
    expect(s.transcript.at(-1)?.picks).toEqual([{ id: 1, label: 'Card', path: 'CardList › Card › h2', tooltip: 'Card.svelte:8' }]);
  });
});

describe('costs', () => {
  it('come with welcome and update on costs.state', () => {
    let s = apply(initialRailState(), msg('welcome', { repo, clockOffset: 0, state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS } }));
    expect(s.costs).toEqual(NO_COSTS);
    s = apply(s, msg('costs.state', { talker: 0.01, call: 0.02, worker: 0.3, since: 0 }));
    expect(s.costs?.worker).toBe(0.3);
    s = apply(s, msg('repo.unknown', { origin: 'http://localhost:9999' }));
    expect(s.costs).toBeNull();
  });
});

describe('clearing the chat', () => {
  it('empties the transcript and takes new settings', () => {
    let s = apply(initialRailState(), msg('welcome', { repo, clockOffset: 0, state: { tasks: [], approvals: [], transcript: [], settings: { preset: 'default', clearChatDaily: true, workerModel: 'sonnet' }, costs: NO_COSTS } }));
    s = apply(s, msg('transcript.final', { turnId: 'u1', role: 'user', text: 'hi', pickIds: [], taskId: null }));
    expect(s.transcript).toHaveLength(1);
    s = apply(s, msg('chat.cleared', {}));
    expect(s.transcript).toEqual([]);
    s = apply(s, msg('settings.state', { preset: 'default', clearChatDaily: false, workerModel: 'sonnet' }));
    expect(s.settings?.clearChatDaily).toBe(false);
  });
});
