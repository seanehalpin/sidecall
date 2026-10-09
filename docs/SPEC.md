# Sidecall — Spec

Working name. A local tool for prototyping by conversation.

## 1. Summary

While looking at a local web prototype in Chrome, you point at elements and talk (or type) to an agent in a right-hand side rail. The agent discusses the change with you, turns it into a task brief, and hands it to Claude Code, which edits the repo in the background while the conversation continues. The project's own dev server hot-reloads the result.

## 2. Goals and non-goals

**Goals**
- Open-call voice conversation with interruptions, plus text input, in one transcript.
- Point at one or many elements with full context; bind pointing to speech by time.
- Background execution via the user's local Claude Code install; the conversation never blocks on execution.
- Works with any repo and dev server. First-class support for React (18, 19) and Svelte 5. No changes to the target project.
- Runs locally on an Apple Silicon Mac. Voice models run locally and are free.

**Non-goals (v1)**
- Non-web targets (simulators, native apps).
- Parallel tasks on one repo.
- Hosting, multi-user, sharing.
- Pushing to remotes or creating PRs.
- Windows and Linux.

## 3. Core workflow

1. Start the daemon (`sidecall start`). Open the localhost prototype in Chrome. Open the rail.
2. First time on a given origin, the rail asks which repo folder it maps to.
3. Start a call or type. Hold ⌥ and click elements while talking: "make [1] match the spacing on [2]".
4. The agent (the **talker**) can read the code to discuss the change. When scope is agreed, it confirms the brief in one sentence and dispatches it.
5. Claude Code (the **worker**) executes. Progress shows in the rail. The page hot-reloads.
6. The talker announces completion at the next pause in conversation. You review on the page, then say "undo that" or queue the next change.
7. If you have hung up, completion and blocked events trigger a notification. Answering starts a call that opens with a summary.

## 4. Architecture

```
┌──────────────────────── Chrome extension (MV3, TS) ────────────────────────┐
│ Side panel (rail UI)   Picker (MAIN world)   Offscreen doc   Service worker │
│        │                     │               (mic + WebRTC)       │         │
└────────┼─────────────────────┼──────────────────────┼─────────────┼─────────┘
         └──────── WebSocket (JSON, token-auth) ──────┼─────────────┘
                                                      │ WebRTC audio
┌──────────────────────────── Daemon (Python, macOS) ─┼───────────────────────┐
│ Server · Repo registry · Pick store · Voice pipeline (Pipecat)              │
│ Talker (Claude Code, read-only) · Worker (Claude Code, edits) · Queue       │
│ Announcer · State                                                           │
└───────────────────────────────┬─────────────────────────────────────────────┘
                                │ reads/writes files
                         Target repo ──► project's own dev server (HMR)
```

- **Control and data:** a WebSocket at `ws://127.0.0.1:<port>` between the extension and the daemon. Messages are defined in §9.
- **Audio:** WebRTC between the offscreen document and the daemon (Pipecat SmallWebRTC transport). Signalling goes over the WebSocket.
- **Target repo:** the daemon and Claude Code read and write files; the project's dev server reloads. The daemon does not start the dev server in v1.

## 5. Extension

Built with WXT. The rail UI uses Svelte 5 (runes). TypeScript strict throughout.

### 5.1 Rail layout (initial; a design pass is pending)

Top to bottom:

1. **Header:** repo name and path, connection status, call button (start / end / mute), settings.
2. **Approvals:** pinned cards awaiting approve or deny.
3. **Transcript:** interleaved entries — user (spoken or typed), agent, and system events (task dispatched, completed, failed). Pick markers render as inline chips; hovering a chip highlights its pin on the page.
4. **Task queue (collapsible):** the running task with a live status line, plus queued tasks. Drag to reorder; cancel.
5. **Pick tray:** chips for the current turn's picks. Remove individually or clear all.
6. **Composer:** text input. A pick made while the composer is focused inserts a chip at the cursor. Enter sends. Shows mic state.

### 5.2 Picker

- Runs as a content script with `"world": "MAIN"` and `run_at: "document_start"`. Default matches: `http://localhost/*` and `http://127.0.0.1/*`; additional origins are configurable.
- It must run in the MAIN world to read framework internals. It also must load before React, so bippy's DevTools-style hook is installed in time.
- It bridges to an isolated-world content script via `window.postMessage` with a per-session nonce. That script relays to the service worker.

| Input | Action |
|---|---|
| Hold ⌥ | Hover-highlight the element under the cursor, with a label showing component name and file:line |
| ⌥-click | Add an element pick |
| ⌥-drag | Add a region pick |
| ↑ / ↓ while hovering | Move selection to the parent or child element |
| Esc | Clear the current turn's picks |

- The overlay lives in a shadow-DOM root at the highest z-index. It sets `pointer-events: none` except while ⌥ is held. Normal page clicks are never intercepted.
- Pins are numbered badges anchored to element rects. They update on scroll, resize and DOM mutation.
- Pin IDs are per-session incrementing integers.
- **Re-anchoring:** after an HMR update or reload, re-resolve each pin by source location, then selector, then text fingerprint. Mark it stale if none match.

### 5.3 Pick payload

```ts
type Pick = {
  id: number;
  createdAt: number; // daemon-clock ms (see §6.9)
  kind: "element" | "region";
  page: {
    url: string; route: string; title: string;
    viewport: { w: number; h: number; dpr: number };
    scroll: { x: number; y: number };
    colorScheme: "light" | "dark";
    consoleErrors: string[]; // last 20
  };
  element?: ElementInfo;
  region?: { rect: Rect; elements: ElementSummary[] }; // top 20 intersecting
  framework: {
    name: "svelte" | "react" | "unknown";
    version?: string;
    source?: { file: string; line: number; column: number; precision: "element" | "component" };
    componentChain: { name: string; type?: string; file?: string; line?: number; column?: number }[];
    props?: unknown; // safe-serialised: depth 3, 4 KB cap, functions → "[fn name]"
  };
  repeat?: { count: number; index: number }; // other instances from the same source location
  screenshots: { crop: string; viewport: string }; // daemon upload refs
};

type ElementInfo = {
  selector: string; altSelectors: string[];
  tag: string; attributes: Record<string, string>;
  text: string;      // trimmed to 500 chars
  outerHTML: string; // trimmed to 4 KB, children collapsed beyond depth 3
  rect: Rect;
  computedStyles: Record<string, string>; // curated list below
};
```

- **Curated computed styles:** display, position, flex and grid properties, gap, margin, padding, width, height, font-family, font-size, font-weight, line-height, letter-spacing, color, background-color, border, border-radius, box-shadow, opacity, z-index.
- **Screenshots:** the service worker captures with `chrome.tabs.captureVisibleTab`, then crops to the element rect plus a margin. A viewport copy gets the element outlined. Encode as WebP or PNG and upload to the daemon over HTTP with the auth token.
- **Console errors:** the picker wraps `console.error`, `window.onerror` and `unhandledrejection` at document_start into a ring buffer.

### 5.4 Source resolution

**Svelte 5**
- Read `el.__svelte_meta.loc` → `{ file, line, column }` with precision `element`.
- Walk `__svelte_meta.parent` for the chain. Entry types include component, each, if, await, key and render. The `parent` chain requires svelte ≥ 5.35.1; older versions give `loc` only.
- If the element has no meta, walk up DOM ancestors until one does.

**React**
- Use bippy to get the fiber from the host element. bippy installs a DevTools-style hook, so it must load at document_start.
- React 17/18: use `_debugSource` on the nearest fiber that has one → precision `element` where available.
- React 19: `_debugSource` was removed. Use the owner stack (`_debugStack`) resolved through source maps fetched from the dev server → precision `component`.
- Build the component chain by walking `fiber.return` and keeping composite fibers. Read props from `memoizedProps`.
- **Verify in spike:** coexistence with the React DevTools extension when both are installed.

**Unknown framework**
- `source` is omitted. The worker locates the code by searching.

**Path normalisation (daemon side)**
- Map absolute paths, `/src/...` paths and dev-server URLs (strip `?t=` and similar query strings) to repo-relative paths. Verify the file exists. If it doesn't, drop `source` and keep the chain.

**Repeat detection**
- Count elements on the page that share the same source location (Svelte) or the same component plus owner location (React). Report the count and this instance's index.

### 5.5 Offscreen document and mic

- An offscreen document (reason `USER_MEDIA`) holds the mic stream and the WebRTC peer connection, and plays agent audio. It survives the panel closing and tab switches.
- Mic constraints: `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }`.
- Mic permission is granted once from the extension options page, because the side panel and offscreen document cannot show a permission prompt.
- **Verify in spike:** that Chrome's echo cancellation removes agent audio played from the offscreen document. Fallback: require headphones.

### 5.6 Service worker

- Owns the WebSocket to the daemon. The daemon sends keepalive pings every 20 s to keep the MV3 service worker alive.
- Routes messages between the picker, the rail and the offscreen document.
- Shows `chrome.notifications` for callbacks and approvals.

## 6. Daemon

Python 3.12, managed with uv. Main dependencies: `pipecat-ai` (with local and MLX extras), `claude-agent-sdk`, `parakeet-mlx`, `mlx-audio`, `pydantic`, `uvicorn` with `starlette` or `fastapi`.

### 6.1 Server and security

- Bind to 127.0.0.1 only.
- **Pairing:** on first run the daemon generates a token and prints a pairing code; the extension stores the token.
- Every WebSocket and HTTP request must present the token, and the WebSocket Origin must be `chrome-extension://<id>`. Reject anything else.

### 6.2 Repo registry and state

- `~/.sidecall/config.toml` maps origins (for example `http://localhost:5173`) to repo paths.
- On an unknown origin the rail prompts. The daemon opens a macOS folder picker via `osascript`, or the user pastes a path.
- Per-repo state lives in `~/.sidecall/repos/<hash>/`, never in the target repo:
  - `notes.md`: persistent context the talker reads at each call start. User-editable. The talker proposes additions; it does not write them silently.
  - `sessions.json`: talker and worker session IDs, for resume.
  - `queue.json`
  - `transcripts/*.jsonl`

### 6.3 Talker (conversation brain)

- A Claude Code session via `claude-agent-sdk` (`ClaudeSDKClient`), with `cwd` set to the repo.
- **Read-only:** allowed tools are Read, Grep and Glob, plus the custom tools below. Edit, Write and Bash are disallowed.
- One persistent client per repo, to avoid startup latency per turn. Resume across days by session ID.
- **System prompt rules:**
  - Output is spoken: short sentences, no markdown or lists, natural contractions.
  - Resolve `[n]` markers against picks.
  - Ask one clarifying question when a request is ambiguous.
  - Before dispatching, confirm the brief in one sentence.
  - Never claim a change is done until the worker reports it.
- **Custom tools** (in-process SDK MCP server):
  - `dispatch_task(brief: Brief) -> task_id`
  - `list_tasks()`, `get_task_status(id)`, `cancel_task(id)`, `reorder_tasks(ids)`
  - `get_pick(id)`: the full payload, when the inline summary isn't enough
  - `request_undo()`: queues a worker task asking it to undo its last change
- **Each user turn includes:** text with markers, compact pick summaries (source, component chain, selector, key styles, repeat info), and crop screenshots as images. At call start, also the queue state and recent task results.

### 6.4 Brief schema

```python
class Target(BaseModel):
    pick_id: int | None
    file: str | None
    line: int | None
    component: str | None
    description: str

class Brief(BaseModel):
    title: str
    goal: str
    targets: list[Target]
    changes: list[str]
    acceptance_criteria: list[str]
    out_of_scope: list[str] = []
    notes: str | None = None
```

### 6.5 Worker (execution)

- A separate `ClaudeSDKClient` per repo, with `cwd` set to the repo and `permission_mode="acceptEdits"`. A `can_use_tool` callback enforces §7.
- One persistent session, so follow-ups ("do the same on the detail view") have context. Resume across days.
- The brief is rendered into a prompt together with the full pick payloads and both screenshots per pick.
- Streamed SDK messages become `task.event` messages (for example "editing src/lib/Card.svelte").
- On result: a summary of what changed plus files touched → `task.completed`. On error → `task.failed` with the reason.
- **Undo:** use Claude Code's default behaviour — ask the worker to undo its last change. No custom git logic in v1.

### 6.6 Queue

- First in, first out per repo, one task running at a time. Each task runs on the result of the previous one.
- Queued tasks can be reordered or cancelled. Cancelling the running task interrupts the worker.
- The queue persists to state.

### 6.7 Announcer

- Event priority: blocked (approval needed) > failed > completed. Progress events are text-only in the rail and never spoken.
- **While a call is active:** hold announcements until the user has not spoken for ≥ 1.2 s and the agent is not speaking. Then the talker produces a one-sentence spoken update. Batch pending events ("two things finished, one needs your OK").
- **No active call:** send a notification ("Card spacing done — tap to talk"). Clicking it opens the rail and starts a call, and the talker opens with the summary.

### 6.8 Voice pipeline (Pipecat)

```
SmallWebRTC in → Silero VAD + Smart Turn v3 → STT: Parakeet TDT 0.6B v3 (parakeet-mlx, word timestamps)
  → TurnAssembler (transcript + picks → marked text, §6.9)
  → TalkerProcessor (custom FrameProcessor wrapping the talker's ClaudeSDKClient, streams text)
  → TTS: Kokoro-82M (mlx-audio) → SmallWebRTC out
```

- **Interruptions enabled:** user speech cancels in-flight TTS and the talker's current response.
- **Swappable via config:** STT can fall back to Whisper large-v3-turbo (MLX); TTS can use Qwen3-TTS or Pocket TTS.
- If Pipecat has no built-in service for a chosen model, write a thin custom service.
- **Latency target:** end of user speech → first agent audio ≤ 1.5 s.

### 6.9 Turn assembly and pick binding

- **Clock:** the daemon clock is authoritative. On connect, the extension measures its clock offset to the daemon, and all `createdAt` values are converted to daemon time.
- **Turn window:** from 3 s before speech starts until the turn ends. Picks with `createdAt` inside the window bind to that turn.
- **Marker placement:** insert `[n]` after the last word that ended before the pick time. Picks made before speech starts are prefixed at the start of the text.
- **Typed turns:** chips inserted at the cursor are the markers. Picks made while the composer is unfocused and no one is speaking go to the tray and attach to the next turn.
- **After a turn:** picks stay referenceable by number (dimmed) until the task they belong to is dispatched or the user clears them.

Example of what the talker receives:

```
User: make [1] match the spacing on [2], and drop the shadow on [1]
Picks:
[1] div.card-header — CardHeader — src/lib/Card.svelte:14:3 (element) — repeated 12×, instance 3 — padding 12px 16px; box-shadow 0 1px 2px …
[2] h3.sidebar-title — Sidebar — src/lib/Sidebar.svelte:22:5 (element) — padding 16px 24px
```

## 7. Permissions and autonomy

**Default preset**

| Action | Behaviour |
|---|---|
| File edits inside the repo | Auto-accept |
| File writes outside the repo | Deny |
| Package installs: `npm/pnpm/yarn/bun add\|install\|i`, `pip install`, `uv add`, `brew install` | Require approval: a card in the rail, a notification, and a spoken "needs your OK". The task waits. |
| `git push` in any form | Deny always. Not overridable in v1. |
| Destructive git: `reset --hard`, `clean -fd`, `checkout -- .`, `restore .` | Require approval |
| Other shell commands in the repo | Allow |

**Presets in settings**
- **Cautious:** approve every shell command.
- **Default:** as in the table above.
- **Permissive:** installs auto-approved.

`git push` stays blocked in every preset.

The command classifier is a pure function with unit tests.

## 8. Persistence

- **Global:** `~/.sidecall/config.toml` and the pairing token.
- **Per repo:** see §6.2.

## 9. Protocol (WebSocket JSON)

Envelope: `{ type, id, ts, payload }`. The daemon's Pydantic models are the source of truth. Export them to JSON Schema in `/protocol`, and generate the TypeScript types in the extension build.

**Extension → daemon**
- `hello {token, extVersion, origin, clockSample}`
- `repo.map {origin, path}`
- `pick.add {Pick}`, `pick.remove {id}`, `picks.clear`
- `user.text {text, pickIds}`
- `call.start`, `call.end`, `call.mute {muted}`
- `task.cancel {id}`, `task.reorder {ids}`
- `approval.respond {id, allow}`
- `settings.update {…}`
- `webrtc.offer {sdp}`, `webrtc.ice {candidate}`

**Daemon → extension**
- `welcome {repo, state, clockOffset}`
- `repo.unknown {origin}`
- `call.state {idle|connecting|listening|thinking|speaking}`
- `transcript.partial {text}`
- `transcript.final {turnId, role, text, pickIds}`
- `agent.text.delta {turnId, delta}`, `agent.text.done {turnId}`
- `task.created {Task}`, `task.event {taskId, kind, text}`
- `task.completed {taskId, summary, files}`, `task.failed {taskId, reason}`
- `task.blocked {taskId, approval: {id, command, reason}}`
- `queue.state {tasks}`
- `notify {title, body, action}`
- `webrtc.answer {sdp}`, `webrtc.ice {candidate}`
- `error {code, message}`

## 10. Repo layout

```
/daemon      Python (uv)
/extension   TypeScript, WXT, Svelte 5
/protocol    generated JSON Schema
/fixtures    svelte5-app, react18-app, react19-app (Vite) — picker test targets
/docs        SPEC.md, PLAN.md
CLAUDE.md
```

## 11. Testing

- **Daemon (pytest):** queue, permission classifier, turn assembly and marker placement (pure functions with timestamp fixtures), path normalisation, protocol round-trips.
- **Extension (vitest):** payload builders and serialisation.
- **Extension (Playwright with the extension loaded):** source resolution against each fixture app.
- **Talker and worker:** integration tests behind a flag, run against a copy of a fixture repo.
- **Voice:** scripted WAV files fed through the pipeline to test STT → turn assembly → talker text. Plus a manual checklist for live audio.

## 12. Risks (de-risk in Milestone 0)

1. Mic capture in the offscreen document, with the permission grant flow.
2. Echo cancellation with agent playback from the offscreen document.
3. MV3 service worker lifetime with a long-lived WebSocket.
4. React 19 source precision, and coexistence with React DevTools.
5. Agent SDK details:
   - image input in streaming mode
   - in-process custom tools
   - first-token latency of a persistent session
   - running on the local Claude Code login
6. Parakeet word timestamps in streaming mode.
7. A Pipecat custom processor wrapping `ClaudeSDKClient`, including interruption handling.

## 13. Open questions

- **React 19 precision:** accept component level plus in-file search (default), or offer an optional per-project dev plugin for element-level lines?
- **Rail design pass.**
- **Name.**