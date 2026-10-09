# Milestone 0 — spikes 1–3 (extension): findings

Throwaway code. Everything lives in `spikes/extension/`.

## Versions

| Thing | Version |
|---|---|
| WXT | 0.21.4 (Vite 6.4.4, `@wxt-dev/browser` 0.3.4 types; the global `browser` is `chrome`-shaped) |
| TypeScript | 5.9.3, pinned. `pnpm add -D typescript` now installs 7.0.x, the Go port, which is untested with WXT |
| vitest | 5.0.3 |
| Playwright | 1.63.0, bundled Chrome for Testing / Chromium **153.0** (`chromium-1243`) |
| Python / websockets | 3.12 (uv) / 17.2 |
| Node / pnpm | 22.17.1 / 10.28.1 |

## How to run

```
cd spikes/extension
pnpm install                 # also runs `wxt prepare`
pnpm build                   # -> .output/chrome-mv3 (load this unpacked in your Chrome)
pnpm test                    # vitest: DSP helpers (RMS, xcorr lag detection, decimation)
pnpm test:e2e                # Playwright Chromium: spikes 1+2 automation -> logs/spike1-2-playwright.json

# Spike 3
pnpm build:ws                # chrome-mv3 (port 8765), chrome-mv3-wsnone (8766), chrome-mv3-wsproto (8767)
scripts/run-sw.sh main  ""       app      8765 30   # 30 min, app-level JSON ping every 20 s
scripts/run-sw.sh none  -wsnone  none     8766 3    # negative: server sends nothing
scripts/run-sw.sh proto -wsproto protocol 8767 3    # negative: only WS protocol ping frames
scripts/run-sw-restart.sh                            # server down 60 s, then back
# Server alone: cd ws-server && uv run server.py --port 8765 --mode app --interval 20 --log ../logs/x.jsonl
# Summarise:    node scripts/summarise-ws.mjs logs/<name>.jsonl
```

`run-sw.sh` starts four things:
- the WS server
- a Python control client (`ws-server/probe.py`)
- a `ps` sampler
- the Playwright Chromium binary, launched **directly**: `--headless=new`, a fresh profile, only this extension loaded, `about:blank`, and **no CDP attached**

Launching Chromium directly matters: an attached debugger keeps service workers alive (Chrome 118+), so driving the browser through Playwright would invalidate the test. Logs go to `logs/*.jsonl` and summaries to `runs/<name>/summary.txt` (both gitignored).

## WXT conventions learned (for M1+)

- **Entrypoints** are discovered by file name in `entrypoints/`: `background.ts`, `sidepanel/index.html`, `options/index.html`.
- **Unlisted pages:** any other `*/index.html`, here `offscreen/index.html`, becomes an unlisted page. It's bundled to `/offscreen.html` but not added to the manifest, which is exactly what `chrome.offscreen.createDocument` needs.
- **Per-page manifest options** go in `<meta name="manifest.*">` tags. For example, `<meta name="manifest.open_in_tab" content="true">` becomes `options_ui.open_in_tab: true`.
- **Side panel:** a sidepanel entrypoint adds the `sidePanel` permission and `side_panel.default_path` automatically.
- **Static files** go in `public/` and are served at `/<name>`; load them with `browser.runtime.getURL('/recorder-worklet.js')`. AudioWorklet modules must be real files, because the extension CSP forbids `blob:` modules.
- **Build modes:** `wxt build -m <mode>` outputs to `.output/chrome-mv3-<mode>`, with `import.meta.env.MODE === '<mode>'`. Handy for variants.
- **Gotchas:**
  - The generated tsconfig enables `noUncheckedIndexedAccess`. It's painful for DSP code, so it's disabled locally.
  - pnpm 10 blocks esbuild's postinstall unless `pnpm-workspace.yaml` has `onlyBuiltDependencies: [esbuild]`.
  - WXT warns if `package.json` has no `version`.
- **Unstable extension IDs:** unpacked extension IDs come from the load path, and every run here got a different one. The daemon's Origin check (SPEC §6.1) needs a stable ID, so **pin a `key` in the manifest**.

## Spike 1 — mic in the extension

**Result: works with the options-page grant. Without the grant, `getUserMedia` in the offscreen document fails immediately with `NotAllowedError: "Permission dismissed"`, and no prompt is ever shown.**

How it works:
- **Creating the offscreen document:** the side panel calls `chrome.offscreen.createDocument({ url: '/offscreen.html', reasons: ['USER_MEDIA','WEB_RTC'], justification })` directly; there's no need to route through the service worker. Only one offscreen document is allowed per extension, so creation is guarded three ways:
  - `chrome.offscreen.hasDocument()` (Chrome 150+)
  - a fallback to `chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'], documentUrls: [url] })`
  - an in-flight promise guard
- **Messaging:** the panel talks to the offscreen document with `chrome.runtime.sendMessage({ target: 'offscreen', … })`. `chrome.runtime` is the only extension API available in an offscreen document.
- **Recording:** the offscreen document calls `getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })` and captures PCM through an AudioWorklet for 5 s.
- **Playback:** it saves a WAV to IndexedDB, which all extension pages share, so the panel can replay or download it. It then plays the recording back with WebAudio in the offscreen document.

Evidence (Playwright Chromium 153 with `--use-fake-device-for-media-stream`; full output in `logs/spike1-2-playwright.json`):

| Case | Result |
|---|---|
| A. Fresh profile, real permission UI, no grant, then Record 5 s | `{ ok:false, name:'NotAllowedError', message:'Permission dismissed', permission:'prompt' }`. The offscreen document has nowhere to show a prompt. |
| B. Fake UI auto-accepts, no options-page grant | Records OK and permission becomes `granted`. The fake UI **masks** the limitation, so automation can't be trusted for this case. |
| C. Fake UI, grant from the options page, then Record 5 s from the panel | `ok:true`: 4.98 s at 48 kHz, RMS −20.8 dBFS (the fake device's beep, so not silent). Settings report EC/NS/AGC `true`. |
| C, continued: close the panel tab and reopen it | The offscreen document is still listed by `runtime.getContexts`, so it survives the panel closing. |

Other findings:
- Automation can't pre-grant the mic to an extension origin. `context.grantPermissions(['microphone'], { origin: 'chrome-extension://…' })` fails with "Permission can't be granted to opaque origins".
- The grant applies to the whole extension origin. Once the options page shows `granted`, the offscreen document and the panel see `granted` too.
- **Don't use the reason `AUDIO_PLAYBACK`**: the docs say it closes the document after 30 s without audio. `USER_MEDIA` and `WEB_RTC` have no lifetime limit.
- **Can the side panel show a prompt itself?** Automation can't tell, because the fake UI auto-accepts and the real panel can't be opened programmatically. The "Try getUserMedia in side panel" button is there for manual step 3. Keep the options-page grant flow either way.

## Spike 2 — echo cancellation

**Result: the tool is built and its pipeline is verified in automation. The actual AEC verdict needs the manual test on a real speaker and mic (steps 8–12 below).**

Research:
- **Historically**, Chrome's software AEC used only **WebRTC remote audio** as its far-end reference, so `<audio>` and WebAudio playback were not cancelled (Chromium issue 687574).
- **"Chrome-wide echo cancellation"**, on by default in most cases since about M106, moved audio processing into the audio service. It uses audio rendered by the whole Chrome instance as the reference, which should cover `<audio>` and WebAudio too.
- **New:** Chromium 153 reports `getCapabilities().echoCancellation = [true, false, "remote-only", "all"]`. `"remote-only"` cancels only WebRTC remote audio; `"all"` cancels all audio.

Test design (in the offscreen document, behind the "Run echo test" button):
- **Playback paths:**
  1. `element`: `new Audio('/speech.wav')`.
  2. `webaudio`: `AudioBufferSourceNode → ctx.destination`.
  3. `webrtc`: a local `RTCPeerConnection` loopback (clip → `MediaStreamDestination` → `pc1.addTrack` → `pc2.ontrack` → `<audio>.srcObject`). This is how Pipecat SmallWebRTC agent audio will play in production.
- **AEC modes:** `true` (the production constraints), `"all"`, `"remote-only"`, and `false` (NS and AGC also off).
- **Each capture:** open the mic, record 1.5 s of pre-roll (to measure the noise floor and let AEC settle), play a 5.8 s speech clip, then record a 0.5 s tail.
- **Measurements:** the noise floor and the level during playback (dBFS), **leak dB = during − floor**, and the peak normalised cross-correlation against the reference clip (0–1, lag ≤ 500 ms, at 16 kHz).
- **Verdict** for each mode against AEC off:

  | Verdict | Rule |
  |---|---|
  | cancelled | leak < 6 dB, or a reduction ≥ 20 dB |
  | partial | reduction ≥ 8 dB |
  | NOT cancelled | anything less |
  | inconclusive | AEC-off leakage is < 10 dB above the floor (speaker too quiet) |

- Every capture and the reference clip can be played or downloaded in the panel. The 12 captures take about 2 min.

Automation result. The fake device has no acoustic path, so leak was about 1 dB everywhere and every verdict was "inconclusive", as designed.
- All 12 captures completed without errors in Chromium 153.
- The WebRTC loopback connects inside the offscreen document.
- `getSettings()` confirmed the requested `echoCancellation` value for every capture.
- The analysis maths is covered by `tests/dsp.test.ts`. It finds a 50 ms echo at −10 dB in noise with a coefficient above 0.9, and unrelated signals score below 0.1.
- Oddity: the first `"all"` capture had a digitally silent pre-roll (floor −180 dBFS). The `"all"` mode may take more than 1 s to start delivering audio, so watch for this if we adopt it.

## Spike 3 — service worker WebSocket

What the service worker (`entrypoints/background.ts`) does:
- Generates a random `bootId` each time it starts.
- Opens `ws://127.0.0.1:<port>/sw` at startup and answers every `{"type":"ping"}` with a `pong`.
- Reconnects with backoff from 1 s up to 30 s.
- Queues lifecycle events and flushes them to the server.
- Runs a 1-minute `chrome.alarms` tick, so a terminated service worker gets woken up (it shows up with a new `bootId`).

All runs: headless Chromium 153, no pages open, no CDP attached.

| Run | Duration | Connects / disconnects | SW boots | Result |
|---|---|---|---|---|
| **main2**: app-level JSON ping every 20 s | 30.0 min | 1 / 1 (teardown only, t=1801.9 s) | **1** | **89/89 pings answered.** Longest gap between SW messages 20.0 s, max SW uptime 1780 s, 29 alarms. The control client also got 89 pongs. The SW was never terminated. |
| main1: first attempt, same config | log ends at 25.7 min | 1 / 0 | 1 | Clean until 25.7 min (77 pongs), then all traffic stopped, with no disconnect and no new boot logged, even when Chrome was killed at 30 min. The server process most likely stalled. Unexplained; the control client and `ps` sampler were added because of it, and the re-run was clean. |
| **none**: server sends nothing | 3 min | 3 / 3 | 3 | SW killed **30 s** after its last message every time (connect at t=2.9 s, close at t=32.9 s, code 1001). It restarts only on the next alarm, then dies again 30 s later. |
| **protocol**: only WS protocol-level ping frames (websockets keepalive, 20 s) | 3 min | 3 / 3 | 3 | Same as **none**: **control-frame pings don't reset the idle timer**. Only data messages do. |
| **restart**: server down for 60 s, then back | 4 min | 2 / – | 2 | The SW died while the server was down; failed reconnect attempts didn't keep it alive. Its in-memory event queue was lost. It reconnected when the 1-minute alarm woke it. |

The server saw `Origin: chrome-extension://<id>`, so the SPEC §6.1 Origin check is feasible.

Conclusions:
- **Only data messages keep the service worker alive.** Chrome's docs (Chrome 116+) say sending or receiving WebSocket messages resets the service worker's idle timer. Confirmed: an open socket alone, or ping/pong control frames, does not.
- **The spec's 20 s ping works, but it must be a JSON data message.** The `websockets` library's built-in keepalive helps detect dead connections, but it doesn't keep the service worker alive.
- **The service worker dies whenever the daemon is down.** Recovery needs a wake source: a `chrome.alarms` alarm with `periodInMinutes: 0.5` (the 30 s minimum since Chrome 120). Anything that must survive a restart belongs in `chrome.storage.session`.
- **Caveat:** this was tested in headless Chromium 153 (Chrome for Testing). Manual step 13 repeats it in real Chrome.

## Manual tests for your own Chrome

Setup:
- Run `cd spikes/extension && pnpm install && pnpm build`.
- In `chrome://extensions`, enable Developer mode, click **Load unpacked**, choose `spikes/extension/.output/chrome-mv3`, then pin the extension.
- Use the built-in speakers and mic, **no headphones**. In macOS Control Center, set Mic Mode to *Standard*.

Steps:
1. **Open the panel.** Click the toolbar icon. **Expected:** the side panel opens and its header shows `Mic permission … prompt`.
2. **Record with no grant.** Click **Record 5 s** before granting anything. **Expected:** no prompt appears, and the JSON shows `ok:false` with `name: "NotAllowedError"` (probably "Permission dismissed"). Note the exact name and message.
3. **Can the panel prompt?** Click **Try getUserMedia in side panel**. **Record what happens:** does a prompt appear (in the panel, or anchored to the address bar), or does it fail immediately, and with which error name? If a prompt appears, dismiss it. If that leaves the state as "denied", reset it from the options tab: site-settings icon → Microphone → Ask.
4. **Grant from options.** Click **Open options (grant mic)**. **Expected:** options opens in a **tab** with state `prompt`. Click **Grant microphone**, then Allow in Chrome's prompt. **Expected:** "Granted. Device: <your mic>" with state `granted`, and the panel header now shows `granted`.
5. **Record.** Click **Record 5 s** and speak for 5 s. **Expected:**
   - After about 5 s your recording plays back (from the offscreen document).
   - The JSON shows `ok:true`, `rmsDb` roughly −45 to −15 while speaking (about −60 or lower if silent), and EC/NS/AGC `true`.
   - A player and a download link appear.
6. **Survives the panel closing.** Close the side panel. In `chrome://extensions`, the extension's "Inspect views" still lists `offscreen.html`. Reopen the panel and record again. **Expected:** it works with no new prompt.
7. *(Optional)* **Recreate.** Click **Close offscreen doc**, then **Record 5 s**. **Expected:** the document is recreated and recording works.
8. **Run the echo test.** Set output volume to about 50–70 % in a quiet room, don't speak, and click **Run echo test**. **Expected:** the progress text steps through element / webaudio / webrtc × on / all / remote-only / off over about 2 min, and you hear the clip 12 times.
9. **Read the table.** **Expected:** each path's AEC `off` row shows `leak dB` ≥ 10. If not, raise the volume and rerun (the verdict will say "inconclusive"). Hypotheses to confirm or refute:
   - `webrtc · AEC true` → **cancelled**. This is the production path. If it isn't cancelled, the SPEC §5.5 fallback (require headphones) applies.
   - `element` / `webaudio · AEC true` → cancelled if Chrome-wide AEC is active. NOT cancelled means only WebRTC audio is used as the reference, which is fine for production as long as all agent audio goes through WebRTC.
   - `AEC "remote-only"` → cancels `webrtc` only.
   - `AEC "all"` → cancels all three.
10. **Listen.** Use the players to compare `webrtc / AEC on` with `webrtc / AEC off`. **Expected:** the clip is clearly audible with AEC off, and absent or faint with AEC on.
11. **Report back.** Send the JSON below the table (or the table and verdict lines), your Chrome version from `chrome://version`, and the value of `chrome://flags/#chrome-wide-echo-cancellation`.
12. *(Optional)* **Double-talk.** Rerun while talking over the clip. In `webrtc / AEC on`, your voice should still be intelligible.
13. *(Optional)* **Spike 3 in real Chrome.**
    - Run `cd ws-server && uv run server.py --mode app --log ../logs/real.jsonl`.
    - Close all extension pages and DevTools, and wait 30 min.
    - Run `node scripts/summarise-ws.mjs logs/real.jsonl`.
    - **Expected:** 1 connect, 0 disconnects, 1 bootId, about 89 pongs.

## Recommended SPEC changes

- **§5.5, replace the first bullet:** "An offscreen document created with reasons `USER_MEDIA` and `WEB_RTC` (never `AUDIO_PLAYBACK`, which closes it after 30 s of silence) holds the mic stream and the WebRTC peer connection, and plays agent audio. Only one may exist per extension; guard creation with `hasDocument()` / `runtime.getContexts`."
- **§5.5, permission bullet, add:** "Without the grant, `getUserMedia` rejects with `NotAllowedError` and no prompt is shown. The rail checks `navigator.permissions.query({name:'microphone'})` before a call and sends the user to the options page, which opens in a tab."
- **§5.5, echo** (confirm after the manual test): "Agent audio is played only from the WebRTC remote track." Also note the `"remote-only"` and `"all"` modes.
- **§5.6, keepalive:** "The ping is an application-level JSON message; protocol ping frames don't keep the service worker alive. A 30 s `chrome.alarms` alarm wakes the service worker to reconnect. State that must survive restarts lives in `chrome.storage.session`."
- **§6.1:** "Pin a manifest `key` so the extension ID, and therefore the Origin, is stable."
- **§9:** add `ping` and `pong`.
