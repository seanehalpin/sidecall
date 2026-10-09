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
  - The daemon offers only a loopback ICE candidate (127.0.0.1). aioice normally skips loopback and advertises LAN and IPv6 addresses, and the macOS firewall can drop inbound UDP to Python on those even when Python is on its allow list. On the dev machine the connection hung at ICE "checking" until a 60 s timeout.
  - The browser is always on the same machine, so loopback is sufficient, and it keeps media off the network, in line with §6.1.
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
- It must run in the MAIN world to read framework internals. It should load before React so bippy's hook sees renderer injection, which is only used to read the React version. Source resolution reads fibers from DOM nodes and does not depend on load order (spike 4).
- **React DevTools coexistence:** bippy 0.7.3 hides its hook from only the first `hasOwnProperty` check, but React DevTools 8 checks twice, so DevTools never installs when the picker loads first. The picker must keep `window.hasOwnProperty('__REACT_DEVTOOLS_GLOBAL_HOOK__')` returning false while bippy's hook is current, so DevTools can replace it.
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
- Chain entries point at where the tag or block is written, i.e. in the *parent* file. Drop entries under `node_modules/` (SvelteKit's root and layout).
- Only the major version is exposed at runtime (`window.__svelte.v`).

**React**
- Use bippy to get the fiber from the host element, then always apply `getLatestFiber()`. Without it, the fiber can be the stale alternate after an update.
- React 17/18: use `_debugSource` on the nearest fiber that has one → precision `element`.
- React 19: `_debugSource` was removed. Resolve the host fiber's `_debugStack` (the `jsxDEV` call site of that element) through the dev server's source map, using bippy `getSource` → precision `element`.
  - React 19 does not refresh `_debugStack` when it reuses a fiber after Fast Refresh. Prefer the latest element's `_debugStack` from the parent host fiber's `memoizedProps.children`.
  - If the frame's module version (`?t=`) is older than the latest HMR import of that file, keep the file and downgrade to precision `component`.
- Build the component chain by walking `fiber.return` and keeping composite fibers. Read props from `memoizedProps`.
- Coexistence with React DevTools is verified, with the shim from §5.2 (spike 4).

**Unknown framework**
- `source` is omitted. The worker locates the code by searching.

**Path normalisation (daemon side)**
- Map paths to repo-relative paths. Inputs arrive in three forms: project-relative (Svelte), absolute filesystem paths (React ≤18) and dev-server URLs with `?t=` and similar query strings (React 19). Verify the file exists. If it doesn't, drop `source` and keep the chain.
- Normalise columns to 1-based. Svelte and React 19 report 0-based columns; React 18 reports 1-based.

**Repeat detection**
- Count elements on the page that share the same source location. The key per framework:
  - Svelte: `loc`.
  - React ≤18: `_debugSource`.
  - React 19: the first user frame of the element's `_debugStack`, with the query stripped. Fall back to component type plus owner.
- Report the count and this instance's index.

### 5.5 Offscreen document and mic

- An offscreen document created with reasons `USER_MEDIA` and `WEB_RTC` holds the mic stream and the WebRTC peer connection, and plays agent audio.
  - Never use `AUDIO_PLAYBACK`, which closes the document after 30 s of silence.
  - Only one offscreen document may exist per extension, so guard creation with `chrome.offscreen.hasDocument()` / `runtime.getContexts` plus an in-flight promise.
  - It survives the panel closing and tab switches (spike 1).
- Mic constraints: `{ echoCancellation: true, noiseSuppression: true, autoGainControl: true }`.
- **Mic permission** is granted once from the extension options page, which opens in a tab (`open_in_tab: true`). The offscreen document can't show a permission prompt: without the grant, `getUserMedia` rejects immediately with `NotAllowedError` ("Permission dismissed"). Before starting a call, the rail checks `navigator.permissions.query({name:'microphone'})` and sends the user to the options page if the mic isn't granted.
- **Echo cancellation:** agent audio is played only from the WebRTC remote track, via an `<audio>` element in the offscreen document. That is the path Chrome's AEC has always used as its far-end reference. Chrome 15x also offers `echoCancellation: "remote-only" | "all"`. A test tool exists (spike 2), but the result needs a manual run on real speakers. Fallback: require headphones.

### 5.6 Service worker

- Owns the WebSocket to the daemon. The daemon sends an application-level JSON `ping` message every 20 s and the service worker replies with `pong`; this held a service worker alive for 30 minutes in spike 3.
  - WebSocket protocol ping frames do **not** keep an MV3 service worker alive. Chrome terminates it 30 s after the last *data* message.
  - When the daemon is down, the service worker is terminated. A `chrome.alarms` alarm every 30 s (`periodInMinutes: 0.5`) wakes it to reconnect.
  - State that must survive service worker restarts lives in `chrome.storage.session`.
- Routes messages between the picker, the rail and the offscreen document.
- **Page context on every typed turn:**
  - On each `user.text`, the service worker attaches `page` (url, route, title, viewport) for the active tab of the last focused window, but only if that tab is on the bound origin.
  - The screenshot comes from `captureVisibleTab` as JPEG (quality 80), downscaled to at most 1568 px on the long edge and uploaded to `POST /api/upload`. Any failure sends the turn with `screenshot: null`; the user's message is never dropped.
  - This needs host permission `<all_urls>`, because `captureVisibleTab` requires `<all_urls>` or `activeTab`, and `activeTab` is only granted by a toolbar click and is lost on navigation (verified). Chrome therefore lists Sidecall as able to read data on all sites.
- Shows `chrome.notifications` for callbacks and approvals, only when the rail isn't open. A notification click calls `chrome.sidePanel.open({windowId})` synchronously in the click handler, with the window id carried in the notification id. If that fails, the window is focused instead.

## 6. Daemon

Python 3.12, managed with uv. Main dependencies: `pipecat-ai` (with local and MLX extras), `claude-agent-sdk`, `parakeet-mlx`, `mlx-audio`, `pydantic`, `uvicorn` with `starlette` or `fastapi`.

### 6.1 Server and security

- Bind to 127.0.0.1 only.
- **Pairing:** on first run the daemon generates a token and prints a pairing code; the extension stores the token.
- Every WebSocket and HTTP request must present the token, and the WebSocket Origin must be `chrome-extension://<id>`. Reject anything else.
- **Uploads:** `POST /api/upload` takes `Authorization: Bearer <token>` plus the extension `Origin`, with a body of `image/jpeg`, `image/png` or `image/webp` up to 8 MB.
  - It returns `{ref}` (32 hex characters). Files are stored in `SIDECALL_HOME/uploads/` and pruned after 7 days, at startup.
  - Errors: 401 bad token, 403 bad origin, 415 unsupported type, 413 too large.
- The extension manifest pins a `key`, so the extension ID (and therefore the Origin the daemon checks) is stable across unpacked installs. Unpacked IDs otherwise change with the load path.

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
- **Read-only:** restrict the built-in tools with `tools=["Read","Grep","Glob"]`. `allowed_tools` holds those plus the full `mcp__sidecall__<tool>` names, and `disallowed_tools` holds Edit, Write, Bash and NotebookEdit. The allow and deny lists alone are not enough: they left about 25 tools available, and in one test the talker spawned a search subagent (spike 5).
- **No narration:** text the talker writes just before a Read/Grep/Glob call ("Let me check the routes…") is dropped. Each assistant message's text is held until we know what follows it: a read tool discards it, while the end of the turn or one of Sidecall's own tools (for example `dispatch_task` after the confirmation sentence) releases it. Haiku ignored a prompt-only rule in testing. The cost is that replies appear a message at a time rather than token by token.
- **Latency settings:** thinking disabled, with a short custom spoken-style system prompt. Register the SDK MCP server with `alwaysLoad: true`; otherwise the tools are deferred behind ToolSearch, which adds 2–3 s. Measured first-text latency on warm turns is about 0.54 s on Haiku and about 1.1 s on Opus.
- **Settings isolation:** `setting_sources=["project"]` and `verbatim_prompts=True` on both the talker and the worker, so the user's global Claude Code settings (for example `defaultMode`) can't override Sidecall's permissions, and assembled prompts don't trigger `@path` or `/slash` expansion.
- One persistent client per repo, to avoid startup latency per turn. Resume across days by session ID (`ResultMessage.session_id`, then `resume=`).
- **System prompt rules:**
  - Output is spoken: short sentences, no markdown or lists, natural contractions.
  - Resolve `[n]` markers against picks.
  - Ask one clarifying question when a request is ambiguous.
  - Before dispatching, confirm the brief in one sentence.
  - Never claim a change is done until the worker reports it.
- **Custom tools** (in-process SDK MCP server; JSON Schemas generated from Pydantic, with `$defs` hoisted to the root of each tool schema, because nested `$defs` break validation in the CLI):
  - `dispatch_task(brief: Brief) -> task_id`
  - `list_tasks()`, `get_task_status(id)`, `cancel_task(id)`, `reorder_tasks(ids)`
  - `get_pick(id)`: the full payload, when the inline summary isn't enough
  - `request_undo()`: queues a worker task asking it to undo its last change
- **Page context:** each turn starts with a page line (`Page: "<title>" — <url> (route …, viewport …)`) and carries the screenshot as an image block.
  - The talker uses them to work out which route file and element the user means, and prefers changing what's on that page.
  - If a change would touch a shared component used in many places (design-system components, layouts, global styles) and the user didn't say "everywhere", it asks "Just on this page, or everywhere?" before dispatching.
  - The page of the dispatching turn is stored on the task (`Task.page`).
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

- A separate `ClaudeSDKClient` per repo, with `cwd` set to the repo and `permission_mode="acceptEdits"`. §7 is enforced in two layers:
  - A PreToolUse hook (matcher `Bash|Edit|Write|NotebookEdit`) runs the classifier on every call. It returns "deny" for git push and out-of-repo writes, and "ask" for actions that need approval. The hook is required because acceptEdits auto-allows some Bash commands (`ls`, `mkdir`, `echo > file`) without consulting `can_use_tool`.
  - `can_use_tool` receives the "ask" cases and awaits the user's approval. It has no timeout. Never wait inside the hook, because hooks time out after 60 s.
- One persistent session, so follow-ups ("do the same on the detail view") have context. Resume across days.
- The brief is rendered into a prompt together with the full pick payloads and both screenshots per pick, plus a "Page the user was looking at" section and that page's screenshot from `Task.page`. The worker scopes the change to that page unless the brief says otherwise.
- The worker keeps Claude Code's `claude_code` preset system prompt and appends Sidecall's rules: keep changes minimal, never push, don't start dev servers, and end with a short summary.
- Streamed SDK messages become `task.event` messages (for example "editing src/lib/Card.svelte").
- On result: a summary of what changed plus files touched → `task.completed`. On error → `task.failed` with the reason.
- **Undo:** use Claude Code's default behaviour — ask the worker to undo its last change. No custom git logic in v1. (Possible later: the SDK's `enable_file_checkpointing` with `rewind_files()` for a deterministic undo; not yet tested.)

### 6.6 Queue

- First in, first out per repo, one task running at a time. Each task runs on the result of the previous one.
- Queued tasks can be reordered or cancelled. Cancelling the running task calls the worker client's `interrupt()`. It stops in about 10 ms with `terminal_reason` `aborted_*`, and the session stays usable for the next task.
- The queue persists to state.

### 6.7 Announcer

- Event priority: blocked (approval needed) > failed > completed. Progress events are text-only in the rail and never spoken.
- **While a call is active:** hold announcements until the user has not spoken for ≥ 1.2 s and the agent is not speaking. Then the talker produces a one-sentence spoken update. Batch pending events ("two things finished, one needs your OK").
- **No active call:** send a notification ("Card spacing done — tap to talk"). Clicking it opens the rail and starts a call, and the talker opens with the summary.

### 6.8 Voice pipeline (Pipecat)

```
SmallWebRTC in → STT: Parakeet TDT 0.6B v3 (parakeet-mlx, offline per VAD segment, word timestamps)
  → user aggregator (Silero VAD + Smart Turn v3)
  → TurnAssembler (transcript + picks → marked text, §6.9)
  → TalkerProcessor (custom FrameProcessor wrapping the talker's ClaudeSDKClient, streams text)
  → TTS: Kokoro-82M (mlx-audio) → SmallWebRTC out
```

- **STT runs offline on each VAD segment** (a custom `SegmentedSTTService`, about 80 ms per segment, in parallel with Smart Turn). parakeet-mlx streaming mode is not used: its word timestamps are relative to each context window and scramble word order on long streams (spike 6). Word times are converted to daemon-clock time from the segment's first-sample time. A turn can span several segments, so their word lists are concatenated.
- **Pipecat ≥ 1.x wiring:** Silero VAD and Smart Turn v3 are configured on the user context aggregator (`LLMUserAggregatorParams(vad_analyzer=…, user_turn_strategies=…)`), not on the transport. Smart Turn v3 (ONNX) ships in the base package. Use `PipelineWorker` and `WorkerRunner`.
- **Interruptions enabled:** user speech cancels in-flight TTS and the talker's current response. The TalkerProcessor streams its response from a background task and, on `InterruptionFrame`, cancels that task and calls `ClaudeSDKClient.interrupt()`. Pipecat does not cancel tasks a processor started itself. The TalkerProcessor must not emit `LLMFullResponseEndFrame` after cancelling, or TTS flushes a half sentence. Measured barge-in: about 350 ms from user speech onset to bot audio stopping.
- **Swappable via config:** STT can fall back to Whisper large-v3-turbo (MLX, Pipecat's `WhisperSTTServiceMLX`); TTS can use Qwen3-TTS or Pocket TTS.
- Pipecat has no Parakeet or MLX-Kokoro service, so the daemon has thin custom ones. All MLX inference (STT and TTS) runs on one single-thread executor, never on the event loop.
- **Kokoro** prepends about 320 ms of silence to every utterance; trim it.
- **Latency target:** end of user speech → first agent audio ≤ 1.5 s.
  - Measured with a stub LLM: about 0.5–0.6 s server-side and about 0.8 s at the WebRTC client (median).
  - That leaves the talker about 0.7 s to its first sentence. TTS works a sentence at a time, so the talker should open with a short sentence.
  - The first turn of a session is slower while MLX compiles kernels; warm up with several audio lengths at startup.
- **Resources:** the voice stack uses about 3 GB of unified memory and warms up in about 4 s from cache. The first model download is about 2.6 GB.

### 6.9 Turn assembly and pick binding

- **Clock:** the daemon clock is authoritative. On connect, the extension measures its clock offset to the daemon, and all `createdAt` values are converted to daemon time.
- **Turn window:** from 3 s before speech starts until the turn ends. Picks with `createdAt` inside the window bind to that turn.
- **Marker placement:** insert `[n]` after the last word that ended before the pick time. Picks made before speech starts are prefixed at the start of the text. Use word *end* times: timestamps have 80 ms resolution and about 0.1 s median error, and the first word's start can be up to about 0.4 s early.
- **Turn continuation:** Smart Turn can end a turn at a mid-sentence pause (seen at 240–420 ms pauses in spike 6). Two rules handle this:
  - If the user resumes within about 1.5 s of a turn end, and the talker hasn't produced audio yet (or only a few words), merge the continuation into the previous turn and re-submit it with the same window, picks and markers, rather than sending a second user message.
  - While a pick is in progress, or within about 1 s after one, don't end the turn on Smart Turn alone.
  - These thresholds need tuning against real speech.
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
| File writes outside the repo | Deny. Our classifier must enforce this: acceptEdits asks `can_use_tool` but lets the write through if allowed. |
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

- **Global:** `~/.sidecall/config.toml`, holding `port`, `token`, `extension_id`, `talker_model`, `worker_model` and the `[repos]` origin → path table. Setting `SIDECALL_HOME` overrides `~/.sidecall`, for example in tests.
- **Per repo:** see §6.2.

## 9. Protocol (WebSocket JSON)

Envelope: `{ type, id, ts, payload }`. The daemon's Pydantic models are the source of truth. Export them to JSON Schema in `/protocol`, and generate the TypeScript types in the extension build.

Field names are camelCase on the wire. The exact shapes live in `daemon/src/sidecall/protocol/` and `/protocol/sidecall.schema.json`.

**Extension → daemon**
- `hello {token, extVersion, origin, clockSample}`
- `pair.request {code}`
- `repo.map {origin, path}`, `repo.browse {origin}`
- `pick.add {Pick}`, `pick.remove {id}`, `picks.clear`
- `user.text {text, pickIds, page}` (page context, §5.6)
- `call.start`, `call.end`, `call.mute {muted}`
- `task.cancel {id}`, `task.reorder {ids}`
- `approval.respond {id, allow}`
- `settings.update {preset}`
- `webrtc.offer {sdp}`, `webrtc.ice {candidate}`
- `pong {seq}`

**Daemon → extension**
- `ping {seq}` (every 20 s; keeps the service worker alive, §5.6)
- `pair.result {token}`
- `welcome {repo, state, clockOffset}`
- `repo.unknown {origin}`
- `call.state {state}` (idle | connecting | listening | thinking | speaking)
- `transcript.partial {text}`
- `transcript.final {turnId, role, text, pickIds, taskId?}`
- `agent.text.delta {turnId, delta}`, `agent.text.done {turnId}`
- `task.created {Task}`, `task.event {taskId, kind, text}`
- `task.completed {taskId, summary, files}`, `task.failed {taskId, reason}`
- `task.blocked {taskId, approval: {id, taskId, command, reason}}`
- `approval.resolved {id, allow}`
- `queue.state {tasks}`
- `notify {title, body, action}`
- `webrtc.answer {sdp}`, `webrtc.ice {candidate}`
- `error {code, message}`

### 9.1 Connection, pairing and repo binding

- **Endpoint:** the daemon listens on `ws://127.0.0.1:7823/ws`. The port is configurable as `port` in `config.toml`.
- **Origin check:** at the WebSocket upgrade, the daemon rejects any `Origin` that isn't `chrome-extension://<id>`. The allowed ID comes from the pinned manifest key (§6.1) and is stored as `extension_id` in `config.toml`; its default is the ID of the key committed in `extension/`.
- **Pairing:**
  - The daemon creates a random `token` on first run and stores it in `config.toml`.
  - `sidecall start` prints a 6-digit pairing code, valid for 10 minutes or until it is used.
  - An extension without a token sends `pair.request {code}` as its first message and receives `pair.result {token}`, which it stores in `chrome.storage.local`.
  - A wrong or expired code returns `error {code: "bad_pairing_code"}`.
- **Hello:**
  - Every connection must authenticate with `hello` before anything else, except `pair.request` and `pong` (an unpaired extension still answers pings).
  - A bad token returns `error {code: "unauthorized"}` and closes the socket with code 4401.
  - Any other message before `hello` (except `pong`) gets the same treatment.
  - A bad `Origin` is rejected before the upgrade is accepted, which the client sees as HTTP 403.
  - A pairing code is invalidated after 10 wrong attempts.
  - `clockOffset` in `welcome` is `daemon_now_ms − clockSample`.
- **Repo binding:**
  - `hello.origin` is the origin of the active tab, for example `http://localhost:5173`. Only `http://localhost:*` and `http://127.0.0.1:*` origins count; anything else is sent as `null`.
  - A mapped origin gets `welcome`. If the mapped folder no longer exists, it gets `repo.unknown` instead.
  - An unmapped origin gets `repo.unknown {origin}`.
  - A `null` origin gets `error {code: "no_origin"}`, and the connection stays authenticated.
  - The extension's own pages (options, or a panel opened as a tab) keep the current binding. New tabs (`about:blank`, `chrome://newtab`) count as `null`.
- **Origin changes:** when the active tab's origin changes, the extension sends `hello` again on the same socket, and the daemon rebinds the connection to the new repo and replies as above.
- **Mapping a repo:**
  - `repo.map` validates that the path is an existing directory, saves it to `config.toml` and replies with `welcome`. A bad path returns `error {code: "bad_path"}`.
  - `repo.browse` opens a macOS folder picker via `osascript`. Choosing a folder behaves like `repo.map`; cancelling returns `error {code: "browse_cancelled"}`.
- **Broadcasts:** a repo's events go to every authenticated connection currently bound to that repo.

### 9.2 Turns, tasks and approvals

**User turns**
- `user.text` → the daemon appends a user entry and broadcasts `transcript.final {role: "user"}`.
- The talker's reply streams as `agent.text.delta` under a new agent `turnId`, then `agent.text.done`, then `transcript.final {role: "agent"}` with the full text and the same `turnId`.
- A new `user.text` while the talker is still replying interrupts that reply first.

**Dispatch**
- When the talker calls `dispatch_task`, the daemon sends `task.created`, then `queue.state`, then `transcript.final {role: "system", taskId}` (for example "Task queued: Red heading").

**Worker progress**
- While a task runs, the daemon sends `task.event` and keeps `queue.state` current. A task's status moves through queued → running → (blocked ↔ running) → completed, failed or cancelled.

**Results**
- On success the daemon sends `task.completed` or `task.failed`, then a system `transcript.final`, then `notify`.
- The extension shows `notify` as a Chrome notification only when the rail isn't open.
- Task results the talker hasn't seen yet are prepended to its next turn as a system note, so it never claims a change it hasn't heard about.

**Approvals**
- When a step needs approval, the daemon sends `task.blocked`, then `notify`.
- The extension replies with `approval.respond`, and the daemon broadcasts `approval.resolved`, which removes the card.
- Denying cancels that step only: the worker is told it was denied and carries on without it.

**Settings**
- `settings.update` gets no reply or broadcast. The preset is saved per repo (in `sessions.json`) and applies from the worker's next tool call.

**Task event kinds**
- `task.event.kind` is `status` (for example "Started"), `edit` (Edit, Write, MultiEdit, NotebookEdit), `tool` (other tool calls, for example "Reading src/x.svelte") or `text` (short snippets of the worker's own text).

**Error codes**
- Beyond the codes above: `bad_message`, `no_repo` (a turn or task message on a connection with no repo bound), `bad_origin`, `unknown_task`, `unknown_approval`, and `not_implemented` (for `pick.*`, `call.*` and `webrtc.*` until their milestones).

**Cancel and reorder**
- `task.cancel` on a queued task marks it cancelled. On the running task, it interrupts the worker.
- `task.reorder` reorders queued tasks only.

**Restart and reconnect**
- `welcome.state` carries the queue (including tasks finished recently), pending approvals, the last 50 transcript entries and the settings.
- On daemon restart, a task that was running goes back to the front of the queue as queued.

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

Status after Milestone 0; evidence is in `docs/spikes.md`.

1. **Mic capture in the offscreen document, with the permission grant flow.** Resolved; manual check passed in the user's Chrome.
2. **Echo cancellation with agent playback from the offscreen document.** Resolved: the user's manual run on real speakers passed. Agent audio still plays only through the WebRTC remote track.
3. **MV3 service worker lifetime with a long-lived WebSocket.** Resolved: JSON pings kept it alive for 30 minutes, plus an alarm wake for daemon restarts.
4. **React 19 source precision, and coexistence with React DevTools.** Resolved: element precision, plus a DevTools shim. The DevTools panel UI passed a manual check.
5. **Agent SDK details** (image input in streaming mode, in-process custom tools, first-token latency of a persistent session, running on the local Claude Code login). Resolved: all four work.
6. **Parakeet word timestamps in streaming mode.** Resolved by not streaming: STT runs offline per VAD segment.
7. **A Pipecat custom processor wrapping `ClaudeSDKClient`, including interruption handling.** Resolved with a stub talker; the real `ClaudeSDKClient` is wired up in Milestone 3.
8. **New: Smart Turn ending turns at mid-sentence pauses.** Open: mitigated by turn continuation (§6.9), and needs tuning against real speech.

## 13. Open questions

- ~~**React 19 precision**~~ Resolved in spike 4: element precision works through `_debugStack` and source maps, with no dev plugin. Component precision remains only as a fallback for a stale stack after HMR.
- **Rail design pass.**
- **Name.**