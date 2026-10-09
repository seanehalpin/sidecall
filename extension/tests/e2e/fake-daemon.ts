// A tiny stand-in for the Python daemon: enough of SPEC §9.1–9.2 to drive the rail.
import type { IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import { WebSocketServer, type WebSocket } from 'ws';
import type { ClientMessage, ClientOf, ClientType, RepoInfo, RepoState, ServerMessage, ServerOf, ServerType } from '../../src/lib/protocol';

export interface Received {
  msg: ClientMessage;
  at: number;
}

type Waiter = { pred: (m: ClientMessage) => boolean; resolve: (m: ClientMessage) => void };

let seq = 0;
export function serverMsg<T extends ServerType>(type: T, payload: ServerOf<T>['payload']): ServerMessage {
  seq += 1;
  return { type, id: `srv${seq}`, ts: Date.now(), payload } as ServerMessage;
}

export class FakeDaemon {
  readonly received: Received[] = [];
  readonly origins: (string | undefined)[] = [];
  readonly sockets = new Set<WebSocket>();
  /** origin → repo; empty until repo.map. */
  readonly repos = new Map<string, RepoInfo>();
  pairingCode = '123456';
  token = 'tok-test';
  state: RepoState = { tasks: [], approvals: [], transcript: [], settings: { preset: 'default' } };
  onUserText: ((ws: WebSocket, m: ClientOf<'user.text'>) => void) | null = null;
  private waiters: Waiter[] = [];
  private constructor(private readonly wss: WebSocketServer) {}

  static async start(): Promise<FakeDaemon> {
    const wss = new WebSocketServer({ host: '127.0.0.1', port: 0, path: '/ws' });
    await new Promise<void>((r) => wss.once('listening', () => r()));
    const daemon = new FakeDaemon(wss);
    wss.on('connection', (ws, req) => daemon.accept(ws, req));
    return daemon;
  }

  get port(): number {
    return (this.wss.address() as AddressInfo).port;
  }

  send(ws: WebSocket, msg: ServerMessage): void {
    // Like the real daemon, welcome.state replays the transcript (SPEC §9.2).
    if (msg.type === 'transcript.final') this.state.transcript.push({ ...msg.payload, ts: msg.ts });
    ws.send(JSON.stringify(msg));
  }

  broadcast(msg: ServerMessage): void {
    for (const ws of this.sockets) this.send(ws, msg);
  }

  of<T extends ClientType>(type: T): ClientOf<T>[] {
    return this.received.filter((r) => r.msg.type === type).map((r) => r.msg as ClientOf<T>);
  }

  waitFor<T extends ClientType>(type: T, pred: (m: ClientOf<T>) => boolean = () => true, timeoutMs = 10_000): Promise<ClientOf<T>> {
    const match = (m: ClientMessage) => m.type === type && pred(m as ClientOf<T>);
    const existing = this.received.find((r) => match(r.msg));
    if (existing) return Promise.resolve(existing.msg as ClientOf<T>);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`timed out waiting for ${type}`)), timeoutMs);
      this.waiters.push({
        pred: match,
        resolve: (m) => {
          clearTimeout(timer);
          resolve(m as ClientOf<T>);
        },
      });
    });
  }

  private accept(ws: WebSocket, req: IncomingMessage): void {
    this.origins.push(req.headers.origin);
    this.sockets.add(ws);
    ws.on('close', () => this.sockets.delete(ws));
    let authed = false;
    this.send(ws, serverMsg('ping', { seq: 1 }));
    ws.on('message', (data) => {
      const msg = JSON.parse(String(data)) as ClientMessage;
      this.received.push({ msg, at: Date.now() });
      this.waiters = this.waiters.filter((w) => {
        if (!w.pred(msg)) return true;
        w.resolve(msg);
        return false;
      });
      switch (msg.type) {
        case 'pair.request':
          if (msg.payload.code === this.pairingCode) this.send(ws, serverMsg('pair.result', { token: this.token }));
          else this.send(ws, serverMsg('error', { code: 'bad_pairing_code', message: 'Wrong or expired code.' }));
          return;
        case 'hello': {
          if (msg.payload.token !== this.token) {
            this.send(ws, serverMsg('error', { code: 'unauthorized', message: 'Bad token.' }));
            ws.close(4401, 'unauthorized');
            return;
          }
          authed = true;
          this.bind(ws, msg.payload.origin);
          return;
        }
      }
      // An unpaired extension still answers pings (it has no token to say hello with).
      if (msg.type === 'pong') return;
      if (!authed) {
        this.send(ws, serverMsg('error', { code: 'unauthorized', message: 'Say hello first.' }));
        ws.close(4401, 'unauthorized');
        return;
      }
      switch (msg.type) {
        case 'repo.map':
          if (!msg.payload.path.startsWith('/')) {
            this.send(ws, serverMsg('error', { code: 'bad_path', message: `Not a directory: ${msg.payload.path}` }));
            return;
          }
          this.repos.set(msg.payload.origin, {
            name: msg.payload.path.split('/').pop() ?? 'repo',
            path: msg.payload.path,
            origin: msg.payload.origin,
          });
          this.bind(ws, msg.payload.origin);
          return;
        case 'repo.browse':
          this.send(ws, serverMsg('error', { code: 'browse_cancelled', message: 'Folder selection cancelled.' }));
          return;
        case 'user.text':
          this.onUserText?.(ws, msg);
          return;
        case 'approval.respond':
          this.broadcast(serverMsg('approval.resolved', { id: msg.payload.id, allow: msg.payload.allow }));
          return;
      }
    });
  }

  private bind(ws: WebSocket, origin: string | null): void {
    if (origin === null) {
      this.send(ws, serverMsg('error', { code: 'no_origin', message: 'No local app in the active tab.' }));
      return;
    }
    const repo = this.repos.get(origin);
    if (!repo) {
      this.send(ws, serverMsg('repo.unknown', { origin }));
      return;
    }
    this.send(ws, serverMsg('welcome', { repo, state: this.state, clockOffset: 0 }));
  }

  async close(): Promise<void> {
    for (const ws of this.sockets) ws.terminate();
    await new Promise<void>((r) => this.wss.close(() => r()));
  }
}
