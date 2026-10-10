# Sidecall

Prototype by pointing and talking. Open your local web app in Chrome, hold ⌥ and click the elements you mean, and say what you want: "make [1] match the spacing on [2]". An agent in a side panel talks the change through with you, then hands it to Claude Code, which edits your repo in the background while your dev server hot-reloads.

The design is in [docs/SPEC.md](docs/SPEC.md) and the build plan in [docs/PLAN.md](docs/PLAN.md).

## Status

**Milestone 2 is built: you can ⌥-click elements on your page, refer to them as `[1]`, `[2]`, and Claude makes the change.** Voice arrives in Milestone 3.

| Milestone | What you get | Status |
|---|---|---|
| 0. Spikes | Each risky piece proven on its own | ✅ Done ([M0 notes](docs/milestones/M0.md)) |
| 1. Daemon core and text rail | Type a change in the side panel; Claude makes it | ✅ Done ([M1 notes](docs/milestones/M1.md)) |
| 2. Picker | ⌥-click elements and reference them as `[1]`, `[2]` | ✅ Built, awaiting your check ([M2 notes](docs/milestones/M2.md)) |
| 3. Voice | Talk instead of typing, with interruptions | Next |
| 4. Settings and persistence | Presets, notes, resume across restarts | |

## Requirements

- An Apple Silicon Mac (the voice models run on MLX).
- Google Chrome.
- [uv](https://docs.astral.sh/uv/) (`brew install uv`). It installs Python 3.12 for you.
- Node 22+ and [pnpm](https://pnpm.io/) 10+.
- [Claude Code](https://claude.com/claude-code), installed and logged in (`claude` in a terminal). Sidecall runs on your Claude Code login, so changes use your Claude usage.

## Run Sidecall

> Start each block from the repo root.

**1. Start the daemon** and leave it running:

```sh
cd daemon
uv sync
uv run sidecall start
```

It prints a 6-digit **pairing code**. You need it once, in step 3.

**2. Build the extension and load it into Chrome:**

```sh
cd extension
pnpm install
pnpm build
```

In `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose `extension/.output/chrome-mv3`. In the file picker, press **⌘ Shift .** to show the hidden `.output` folder. Pin the Sidecall icon.

If you're working on the extension itself, run `pnpm dev` instead and load `extension/.output/chrome-mv3-dev`. It rebuilds on save; click reload on the extension in `chrome://extensions` to pick up changes.

**3. Open your app and pair:**

1. Start your app's dev server and open it in Chrome on `localhost` (for example a fixture: `cd fixtures/svelte5-app && pnpm install && pnpm dev`).
2. Click the Sidecall icon. The side panel opens.
3. Enter the pairing code from step 1.
4. When it asks which folder the app's code lives in, paste the repo path or click **Choose folder…**. Sidecall remembers it for that `localhost` address.

**4. Ask for a change.** Type, for example, "make the heading on the home page red", and press Enter. The agent confirms what it's going to do and queues a task. Claude Code edits the code in the background, the page hot-reloads, and the panel shows when it's done.

**5. Point at things.** With your app open in the active tab, hold **⌥ (Option)** and move the mouse: the element under it is highlighted, labelled with its component and `file:line`.

- **⌥-click** picks an element and **⌥-drag** picks a region. A numbered pin appears on the page, and a chip appears in the pick tray in the side panel.
- **↑ / ↓** (while holding ⌥ over an element) move the selection to its parent or child. **Esc** clears the current picks.
- Refer to picks in your message as `[1]`, `[2]`, or click a chip in the tray to insert its marker. For example: "make [1] match the spacing on [2]".
- Picking one item in a repeated list (say one of twelve cards) tells the agent it's one instance of a template, so the edit goes into the shared component rather than one copy.
- Hovering a chip highlights its pin on the page. Pins stay on their elements through hot reloads and page reloads.

**What else to know**

- **Installing packages** shows an approval card in the panel. Nothing installs until you click Approve.
- **`git push`** is always refused.
- **Pointing at the fixtures:** Claude edits the files in place. If you use `fixtures/` inside this repo, those are tracked files; undo with `git checkout fixtures/`.

## Try the spikes

Each spike is its own project.

> **Start every block below from the repo root.** The paths are relative to it. If you're already inside another spike, `cd` back to the root first (for example `cd ../..` from `spikes/voice`).

### Voice loop: talk to a stub agent

This is the most fun one. It's a local speech-to-text → agent → text-to-speech loop. The "agent" just repeats what you said, for now.

```sh
cd spikes/voice
uv sync
uv run python server.py
```

1. Wait for `models ready` in the terminal. The first run downloads about 2.6 GB of models, which takes a couple of minutes. After that, startup takes about 4 s.
2. Open http://localhost:7860 in Chrome and click **Connect**, then allow the mic.
3. Say something. You'll hear "You said: …" back, and the page shows the transcript and the latency.
4. Tick **slow / long replies** and talk over the reply to test interrupting.

### Extension: mic, echo test, background connection

```sh
cd spikes/extension
pnpm install
pnpm build
```

1. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and choose `spikes/extension/.output/chrome-mv3`.
2. Pin the extension and click its icon. The side panel opens.
3. Click **Open options (grant mic)**, then **Grant microphone**, and allow it. This step is required: Chrome won't show a mic prompt from the side panel itself.
4. Back in the panel:
   - **Record 5 s** records your voice and plays it back.
   - **Run echo test** plays a clip through your speakers while recording, then reports whether the clip leaked into the mic. Use speakers, not headphones, at 50–70 % volume, and stay quiet for about 2 minutes.

### Picker: ⌥-click an element to find its source

First start a fixture app and build the picker:

```sh
cd fixtures/react19-app      # or svelte5-app, react18-app
pnpm install
pnpm dev                     # React 19 → http://localhost:5175
```

In a second terminal:

```sh
cd spikes/picker
pnpm install
pnpm build
```

1. In `chrome://extensions`, click **Load unpacked** and choose `spikes/picker/ext`.
2. Open the fixture in Chrome, then open DevTools → Console.
3. Hold ⌥ and click a card title. The console logs `[sidecall] pick` with the file, line and component chain.

To run the automated checks across all three fixtures: `cd spikes/picker && node test.mjs`.

### Agent SDK

These scripts make real Claude calls on your Claude Code login, so they use some of your usage (about $1.50 for the full set).

```sh
cd spikes/agent-sdk
uv sync
uv run python s1_accept_edits.py    # s1 to s6; see FINDINGS.md for what each one checks
```

## Fixture apps

Small target apps (a sidebar, a list of 12 cards and a form) used to test the picker and, later, Claude's edits.

| App | Command | URL |
|---|---|---|
| Svelte 5 (SvelteKit) | `cd fixtures/svelte5-app && pnpm install && pnpm dev` | http://localhost:5173 |
| React 18 (Vite) | `cd fixtures/react18-app && pnpm install && pnpm dev` | http://localhost:5174 |
| React 19 (Vite) | `cd fixtures/react19-app && pnpm install && pnpm dev` | http://localhost:5175 |

If a port is taken, set another one: `PORT=5273 pnpm dev`.

## Troubleshooting

- **⌥-click does nothing.** The picker only runs on `http://localhost:…` and `http://127.0.0.1:…` pages, and the extension needs reloading after a rebuild (click the reload icon in `chrome://extensions`, then refresh the page). With Svelte, wait for the page to finish loading: source locations are attached when it hydrates.
- **A pick shows no file or line.** The app isn't a Svelte 5 or React 18/19 dev build, or it's a production build. The agent will search by class names and text instead.
- **The panel says "Daemon offline".** Start it with `cd daemon && uv run sidecall start`. The panel reconnects within about 30 s.
- **The panel says "No local app".** The active tab has to be a `http://localhost:…` or `http://127.0.0.1:…` page.
- **Lost the pairing code.** Restart the daemon; it prints a new one each time it starts.
- **"Port already in use" for a fixture.** Another dev server holds 5173 or 5174. Run it with `PORT=<other> pnpm dev`.
- **Recording fails with `NotAllowedError`.** The mic hasn't been granted to the extension. Use **Open options (grant mic)** in the side panel and allow it there.
- **The voice page is stuck on "connecting…".** Make sure you're running the latest `server.py`, which keeps WebRTC on 127.0.0.1 (the macOS firewall can block it on your network address), then restart it with Ctrl+C and `uv run python server.py`, and reload the page.
- **The voice page connects but you hear nothing back.** Check that the terminal shows `models ready`, and that Chrome isn't muted for the tab.
- **The voice agent cuts you off mid-sentence.** This is a known issue: pausing for about half a second can end your turn. A fix is planned for Milestone 3.
- **Loading the extension fails after a code change.** Re-run `pnpm build`, then click the reload icon on the extension in `chrome://extensions`.

## Repo layout

```
daemon/      Python daemon (from Milestone 1)
extension/   Chrome extension (from Milestone 1)
protocol/    JSON Schema generated from the daemon's models
fixtures/    Svelte 5, React 18 and React 19 test apps
spikes/      Milestone 0 experiments (throwaway); each has a FINDINGS.md
docs/        SPEC.md, PLAN.md, spikes.md, milestones/
```

`daemon/`, `extension/` and `protocol/` arrived with Milestone 1.
