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
5. **Pick tray:** chips for the live picks, between the task queue and the composer. Each shows `[n]` and the component (or tag.class), with a tooltip of file:line, precision and repeat info.
   - Hovering a chip highlights its pin on the page.
   - Clicking a chip inserts `[n]` into the composer at the cursor.
   - Chips can be removed individually, or all at once with "Clear all". Picks from earlier turns show dimmed.
   - `[n]` markers in sent messages render as inline pills in the transcript, with the same tooltip.
6. **Composer:** a plain textarea. Enter sends. A picks hint shows under it while picks exist.
   - New picks go to the tray rather than into the composer: clicking on the page blurs the side panel, so the composer is never focused when a pick lands.
   - Inline chips inside the text were dropped because a textarea can't hold them.

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
- Pin IDs are per-session incrementing integers, assigned by the service worker and persisted in `chrome.storage.session`.
- Input is intercepted only while ⌥ is held: window-level capture listeners stop the event, so ⌥-click never submits a form, toggles a button or follows a link. Plain clicks are never touched. ↑/↓ are handled only while ⌥ is held over a hovered element.
- The picker posts `ready` at DOMContentLoaded, after re-sending its handshake. The nonce is a stray-message guard, not a secret: any script on the page can read it, which is accepted because the page being prototyped is the user's own.
- **Extension reloads:** Chrome doesn't re-inject content scripts into tabs that were already open, and an old MAIN-world picker keeps running there with a dead bridge (hover works, picks vanish). So on `runtime.onInstalled` the worker re-injects the bridge and picker into open localhost tabs (`scripting` permission). The newest picker instance in a page takes over and older ones go inert. A pick that gets no pin back within 5 s shows a hint ("check the side panel is connected, or refresh the page").
- `Anchor.region` (for region picks) is in document coordinates (viewport rect plus scroll at pick time), so region pins follow scrolling.
- **Re-anchoring:** after an HMR update or reload, re-resolve each pin by source location (plus its index among elements sharing that source), then selector, then text fingerprint. Mark it stale if none match.
  - DOM mutations are debounced (about 120 ms). After a full reload the worker restores pins from the stored anchors, and an unmatched pin only becomes stale after a 4 s grace period (it may not have rendered yet).
  - Verified on all three fixtures: pins stay on the same element across an HMR edit, and re-anchor within about 100 ms after a reload.

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
- **Screenshots:** the service worker asks the page to hide its overlay (and waits for the page's ack, up to 500 ms), then calls `chrome.tabs.captureVisibleTab` once.
  - From that single capture it derives a **crop** (the element or region rect plus a 16 px margin, clipped to the viewport) and a **viewport copy** downscaled to at most 1568 px with the element outlined. The picker's own pins and highlights aren't in either image.
  - Both are encoded as JPEG (quality 80) and uploaded to the daemon over HTTP with the auth token. The pick is sent with `screenshots: null` if capture or upload fails, so a pick is never lost.
  - Captures are spaced at least 600 ms apart (Chrome limits them to about two a second). Crop and outline math use one uniform scale (`bitmap width / viewport width`).
- **Console errors:** the picker wraps `console.error`, `window.onerror` and `unhandledrejection` at document_start into a ring buffer.

### 5.4 Source resolution

**Svelte 5**
- Read `el.__svelte_meta.loc` → `{ file, line, column }` with precision `element`.
- Walk `__svelte_meta.parent` for the chain. Entry types include component, each, if, await, key and render. The `parent` chain requires svelte ≥ 5.35.1; older versions give `loc` only.
- If the element has no meta, walk up DOM ancestors until one does. The same fallback applies to React: when the target has no framework metadata of its own, the nearest ancestor's source is used with precision `component`.
- Chain entries point at where the tag or block is written, i.e. in the *parent* file. Drop entries under `node_modules/` (SvelteKit's root and layout).
- Only the major version is exposed at runtime (`window.__svelte.v`).

**React**
- Use bippy to get the fiber from the host element, then always apply `getLatestFiber()`. Without it, the fiber can be the stale alternate after an update.
- React 17/18: use `_debugSource` on the nearest fiber that has one → precision `element`.
- React 19: `_debugSource` was removed. Resolve the host fiber's `_debugStack` (the `jsxDEV` call site of that element) through the dev server's source map → precision `element`. (The implementation decodes the source map itself with `source-map-js` and its own stack parser, cached per module URL; the spike found this agrees with bippy's `getSource` on every pick.)
  - React 19 does not refresh `_debugStack` when it reuses a fiber after Fast Refresh. Prefer the latest element's `_debugStack` from the parent host fiber's `memoizedProps.children`.
  - If the frame's module version (`?t=`) is older than the latest HMR import of that file, keep the file and downgrade to precision `component`.
- Build the component chain by walking `fiber.return` and keeping composite fibers. Read props from `memoizedProps`.
- Coexistence with React DevTools is verified, with the shim from §5.2 (spike 4).

**Unknown framework**
- `source` is omitted. The worker locates the code by searching.

**Path normalisation (daemon side)**
- Map paths to repo-relative paths. Inputs arrive in three forms: project-relative (Svelte), absolute filesystem paths (React ≤18) and dev-server URLs with `?t=` and similar query strings (React 19). Verify the file exists. If it doesn't, drop `source` and keep the chain.
- Normalise columns to 1-based. Svelte and React 19 report 0-based columns; React 18 reports 1-based.
- **Specifics (daemon, once, on `pick.add`):**
  - Accepted forms: project-relative paths, absolute filesystem paths, dev-server URLs (query and fragment dropped, percent-decoded), root-relative URL paths, `file://`, and Vite's `/@fs/<abs>` and `/@id/` forms.
  - Symlinks and `..` are resolved. A path outside the repo, under `node_modules`, or that doesn't exist yields no source.
  - When `source` fails verification it is set to null, and the chain entry keeps its name with file, line and column nulled. Chain entries under `node_modules` are dropped.
  - Column +1 for Svelte and React ≥19; React ≤18 and unknown are unchanged.

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
- **Mic permission** is granted once from the extension options page, which opens in a tab (`open_in_tab: true`). The offscreen document can't show a permission prompt: without the grant, `getUserMedia` rejects immediately with `NotAllowedError` ("Permission dismissed"). Before opening the mic, the offscreen document checks `navigator.permissions.query({name:'microphone'})`. If the mic isn't granted (or getUserMedia rejects with NotAllowedError), the worker ends the call, shows an error with a link to settings, and opens the options page.
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
- **Two talkers.** Typed turns use one resumed session per repo on `talker_model` (default `sonnet`), with Read/Grep/Glob, screenshots and pick crops, as described above.
- **Costs in the UI.** Each talker and worker result adds its `turn_cost` to the repo session's `Costs` (typed chat, calls, worker, plus when counting started), kept in memory since the daemon started. The total goes out in `RepoState.costs` on welcome and as `costs.state` after each counted turn. The ⋯ menu shows the total and the breakdown. Turns that end without a result have no known cost and aren't counted.
- **Call talker.** Spoken turns and announcements use `CallTalker` on `call_talker_model` (default `haiku`), a fresh client for each call, never resumed:
  - A short system prompt (acknowledge, clarify, dispatch, announce; one spoken sentence). No `CLAUDE.md` (`setting_sources=[]`), no file tools, and only `dispatch_task`, `cancel_task` and `request_undo`.
  - No images: the page screenshot and pick crops cost about 4x per turn. Pick summaries are text, and the dispatched task still carries the page and pick snapshots, so the worker gets every image.
  - Every turn starts with a `[Worker]` status line (running, queued, the last three finished with one-line outcomes). The first turn of a fresh client also gets `[Earlier in this call]`: the call's last 10 user and agent entries.
  - **Hang-ups don't drop dispatches.** When the reply stream closes early (hang-up, pipeline teardown, barge-in) the turn is wound down, not interrupted: the message being written is finished, any tool call in it (the dispatch the user just heard confirmed) runs, then the turn stops at the next request. A correction after a dispatch becomes a "Follow-up: <title>" task; nothing is un-dispatched. `scripts/hangup_repro.py` reproduces the original bug live.
  - A call opens with an announcement when there are unannounced results or tasks still in flight; the `[Worker]` line gives the talker what's running.
  - After 20 turns the client is replaced and the new one is seeded the same way, so context stays small. It closes when the call ends, after any turn in progress.
  - Measured live: first text 0.8 to 1.0 s, about $0.002 per chat turn and $0.009 per dispatch turn (below Haiku's cache minimum, so nothing is cached).
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
  - `get_pick(id)`: the full normalised payload as JSON (camelCase wire names), when the inline summary isn't enough; an error if the id is unknown or was cleared
- **Pick summaries** in the turn use the format `[n] tag#id.class — Component — file:line:col (precision) — repeated N×, instance k — key styles`. A region is `[n] region WxH at (x,y) — contains: …`. A missing source reads `source unknown`, and an id the daemon no longer holds reads `[n] (no longer available)`. `instance k` is the 0-based `repeat.index` plus one.
- **Crop screenshots** go as image blocks labelled `[n] crop`, up to 6 images per turn including the page screenshot.
- **Repeated picks:** when a pick has `repeat.count > 1`, the talker treats it as one instance of a repeated template and changes the shared template or component, unless the user says "just this one".
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
- **Session rotation** (`handoff.py`). The worker resumes one session through a burst of tasks, so follow-ups ("do the same on the detail view") have context and the prompt cache stays warm. Before a task it starts a new session instead if any of these hold (each configurable in `config.toml`; the reason is logged):
  - more than `worker_idle_minutes` (15) since the last result: the cache is cold, so resuming would pay cache writes on the whole history;
  - the last request read more than `worker_max_context_tokens` (120k), measured from the last assistant message's usage (the result's usage sums every request in the turn);
  - the session's summed turn costs exceed `worker_max_session_cost` ($15). `sessions.json` keeps that sum, the last result time and the context size alongside the session id.
- **Task log.** The daemon (not a model) appends one line per finished task to `<repo state>/task-log.jsonl`: time, title, the brief's goal plus the user's words, status, summary or error, and files. A new session's first prompt starts with the last 20 entries, `git status --short` and `git log --oneline -10`. `CLAUDE.md` isn't repeated there, since the worker already loads project settings. Compaction isn't used; rotation keeps sessions bounded.
- The brief is rendered into a prompt together with the full pick payloads and both screenshots per pick, plus a "Page the user was looking at" section and that page's screenshot from `Task.page`. `Task.request` carries what the user said since the previous hand-over, verbatim, and the prompt puts it first as the source of truth: a brief is the talker's summary and can lose detail. The worker does the whole job and checks it against the acceptance criteria (plus a quick type check or lint) before reporting; only its summary is kept short. It prefers page-scoped changes and edits shared components when the brief records that the user confirmed it (the talker asks first), for repeated instances, or, failing a clean scoped option, says so in its summary.
  - **Task snapshots:** on dispatch, `Task.pickIds` is the brief's target pick ids plus the turn's picks (existing ones only). The Pick objects are snapshotted to `<repo state>/tasks/<task_id>/picks.json`, and the worker loads them when it runs, so `picks.clear`, id reuse and daemon restarts can't change a queued task.
  - **Pick payload in the prompt:** each pick is rendered fully and compactly under "Picked elements" (selector and alternates, tag, attributes, text, trimmed outerHTML, all computed styles, framework and component chain with file:line:col, props, repeat, page). For a repeated pick the worker edits the template that renders all instances, not one copy, and leaves unrelated instances and pages alone.
  - **Images:** the page screenshot first, then for the first 6 picks both screenshots (crop, then viewport), then crops only, with an overall cap of 20.
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
  - **Debounce:** every turn is held `TURN_DEBOUNCE_MS` (500 ms, env `SIDECALL_TURN_DEBOUNCE_MS`) before the talker is asked, using the same hold as a pick. Speech in the window continues the turn with no API call, and is logged as `turn debounced` with the gap. Speech is reported only after `VAD_START_SECS` (0.2 s), so the window catches restarts within about 300 ms; later ones fall to the continuation rule above, at the cost of an aborted talker call. VAD `stop_secs` stays 0.2, as Pipecat recommends with Smart Turn.
  - These thresholds need tuning against real speech.
- **Typed turns:** picks go to the tray; clicking a chip inserts its `[n]` marker, and typing `[n]` works too. The service worker, not the rail, decides a turn's `pickIds`: every live pick that isn't dimmed, plus any `[n]` referenced in the text that exists.
- **After a turn:** picks stay referenceable by number (dimmed) until the task they belong to is dispatched or the user clears them.
  - The dispatched `Task` lists them in `pickIds` (the brief's target picks plus the turn's), and the extension then removes them from the tray and the page.
  - Esc on the page removes only the current turn's picks; "Clear all" in the tray clears everything (`picks.clear`).
  - The daemon keeps picks in memory per repo (capped at 100, newest wins on a duplicate id). On reconnect the extension re-sends every live pick.

Example of what the talker receives:

```
User: make [1] match the spacing on [2], and drop the shadow on [1]
Picks:
[1] div.card-header — CardHeader — src/lib/Card.svelte:14:3 (element) — repeated 12×, instance 3 — padding 12px 16px; box-shadow 0 1px 2px …
[2] h3.sidebar-title — Sidebar — src/lib/Sidebar.svelte:22:5 (element) — padding 16px 24px
```


### 6.10 Voice turns (Milestone 3)

- **Talker in calls:** spoken turns use the repo's talker, with the same session, lock, task notes, Picks section and crops as typed turns.
  - Spoken turns stream text straight through, without the typed-turn narration hold; the voice note allows a short (2–4 word) acknowledgement before the talker reads code.
  - An interruption cancels TTS and calls `interrupt()`; the agent transcript records what was heard plus " …", and the talker is told what was heard on its next turn.
  - Typed `user.text` during a call goes through the same serialised talker; typed replies during a call are shown, not spoken.
- **Page context in calls:** `call.page {page}` is sent when the call starts listening, on a tab change, and when a new utterance begins (at most one every 2 s, bound-origin tabs only). A spoken turn's page is the latest call page, else the first pick's page, else the last typed turn's page.
- **Pipeline:** STT → SegmentTap → user aggregator → TalkerProcessor → TTS → output → OutputTap.
  - The TalkerProcessor groups sentences itself and flushes when the talker's text pauses, and at the first clause of a long opening sentence.
  - STT trims trailing silence to 0.2 s, because Parakeet otherwise stretches the last word about 0.35 s late. Segment times come from a sample clock, not frame arrival.
  - The talker connects when the call connects.
- **Measured (server side, haiku):**
  - Median 1.2 s and p90 1.5 s from end of speech to first audio.
  - Barge-in about 355 ms at VAD `start_secs` 0.2 (`SIDECALL_VAD_START_SECS` overrides; 0.15 gives about 308 ms).
- **Pick binding:**
  - Constants: a 3 s pre-speech window, 1.5 s continuation, a 1 s hold after a pick, and `LATE_PICK_MS` = 1 s.
- **Re-warm after idle:** when a call's offer arrives and the MLX models have been idle for over 2 minutes, one short phrase goes through Kokoro and that audio through Parakeet in the background, output discarded. After hours idle the first STT pass had taken 7.85 s and Kokoro's first reply came back empty. A warm pass takes about 0.2 s and finishes during ICE and the 600 ms opening delay.
  - A pick reaching the daemon late (made during the turn or up to 1 s after it) re-submits the turn.
  - A merged turn re-sends `transcript.final` with the same turnId, which replaces the entry.
- **Announcer:**
  - During a call, task results are spoken after 1.2 s of quiet, batched by priority, instead of `notify`. Approvals also notify with action "open".
  - With no call, results notify with action "call"; clicking starts a call that opens with a summary.
  - Any talker turn marks the notes it carried as seen.

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

- **Global:** `~/.sidecall/config.toml`, holding `port`, `token`, `extension_id`, `talker_model`, `call_talker_model`, `worker_model` (models are written only when they differ from the default) and the `[repos]` origin → path table. Setting `SIDECALL_HOME` overrides `~/.sidecall`, for example in tests.
- **Per repo:** see §6.2.

## 9. Protocol (WebSocket JSON)

Envelope: `{ type, id, ts, payload }`. The daemon's Pydantic models are the source of truth. Export them to JSON Schema in `/protocol`, and generate the TypeScript types in the extension build.

Field names are camelCase on the wire. The exact shapes live in `daemon/src/sidecall/protocol/` and `/protocol/sidecall.schema.json`.

**Extension → daemon**
- `hello {token, extVersion, origin, clockSample}`
- `pair.request {code}`
- `repo.map {origin, path}`, `repo.browse {origin}`
- `pick.add {Pick}`, `pick.remove {id}`, `picks.clear` (none gets a reply; `remove` of an unknown id is a no-op)
- `user.text {text, pickIds, page}` (page context, §5.6)
- `call.start`, `call.end`, `call.mute {muted}`
- `call.page {page: PageContext}`: during a call, the page in view (screenshot included). Sent when the call reaches listening, on tab activation/navigation, and when a new utterance starts; at most one per 2 s, skipped off the bound origin. A spoken turn's page is the latest call page, else the first pick's page, else the last typed turn's page. Ignored without an active call.
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
- Beyond the codes above: `call_active`, `no_call`, `call_failed`, `bad_message`, `no_repo` (a turn or task message on a connection with no repo bound), `bad_origin`, `unknown_task`, `unknown_approval`, and `not_implemented` (now unused).

**Calls**
- Signalling is non-trickle: the offer is sent after ICE gathering completes, and the daemon ignores `webrtc.ice`.
- `call.state`, `transcript.partial` and `webrtc.answer` go only to the connection that owns the call; `transcript.final` and `agent.text.*` go to everyone bound to the repo.
- `transcript.partial` is "…" while VAD hears speech, then the segment text so far.
- Measured through the real extension (headless, recorded voice as the mic): 877 ms from end of speech to first audio on the first turn.

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