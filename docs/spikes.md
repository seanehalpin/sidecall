# Milestone 0 — spike findings

This file answers each risk in SPEC §12. Detailed evidence, exact APIs, versions and run instructions live in each spike's `FINDINGS.md`:

| Spike | Code | Detail |
|---|---|---|
| 1–3. Mic, echo cancellation, service worker WebSocket | `spikes/extension/` | [FINDINGS](../spikes/extension/FINDINGS.md) |
| 4. Picker source resolution | `spikes/picker/`, `fixtures/*` | [FINDINGS](../spikes/picker/FINDINGS.md) |
| 5. Agent SDK | `spikes/agent-sdk/` | [FINDINGS](../spikes/agent-sdk/FINDINGS.md) |
| 6. Voice loop | `spikes/voice/` | [FINDINGS](../spikes/voice/FINDINGS.md) |

All of these were measured on an Apple M4 Pro with 24 GB, on 2026-10-09. The browser runs used Playwright's Chrome for Testing 153.

## Summary

| # | Risk | Answer | Status |
|---|---|---|---|
| 1 | Mic in the offscreen document | Works once the options page grants the mic. Without the grant: instant `NotAllowedError`, no prompt. | Resolved; manual check pending |
| 2 | Echo cancellation | Test tool built. It compares `<audio>`, WebAudio and WebRTC playback across AEC modes. | **Open: needs a manual run** |
| 3 | Service worker WebSocket lifetime | 30 min with no disconnect and no restart, using JSON pings every 20 s. Protocol pings don't work. | Resolved |
| 4 | React 19 precision, DevTools coexistence | React 19 gets **element** precision. DevTools breaks without a shim and works with it. | Resolved; panel UI manual check pending |
| 5 | Agent SDK | Image input, custom tools, local login, resume and interrupt all work. Warm first-text latency is 0.54 s on Haiku and 1.1 s on Opus. | Resolved |
| 6 | Parakeet streaming timestamps | Broken past the context window. Transcribe each VAD segment offline instead (about 80 ms). | Resolved by design change |
| 7 | Pipecat processor wrapping the talker, with interruptions | `InterruptionFrame` reaches the processor; cancel the background task there. Barge-in takes about 350 ms. | Resolved (stub talker) |
| new | Smart Turn ends turns at mid-sentence pauses | 5 of 8 synthetic utterances were cut off at pauses of 240–420 ms. | **Open: tune with real speech** |

## 1. Mic in the offscreen document

- **Works:** the side panel creates an offscreen document (reasons `USER_MEDIA` + `WEB_RTC`), which records 5 s and plays it back. With the options-page grant, it recorded −20.8 dBFS from the fake device, with EC/NS/AGC confirmed on. The document survives the panel closing.
- **Fails without the grant:** `getUserMedia` rejects immediately with `NotAllowedError: Permission dismissed`, and no prompt appears.
- **Automation can't fully verify this:** Playwright's fake-UI flag hides the failure (it auto-grants), and `grantPermissions` can't target `chrome-extension://` origins. The no-grant behaviour above came from a run with the real permission UI.
- **Don't use the `AUDIO_PLAYBACK` reason:** it closes the document after 30 s of silence.
- **Not yet known:** whether the side panel itself can show a prompt. Manual step M0-3.

## 2. Echo cancellation

The test tool plays a speech clip through three paths: an `<audio>` element, WebAudio, and a WebRTC loopback played as a remote track (the production path). It runs each path with `echoCancellation` set to `true`, `"all"`, `"remote-only"` and `false`, and reports leak dB above the noise floor, cross-correlation against the clip, and a verdict. Chromium 153 newly offers the `"remote-only"` and `"all"` modes.

The automated run completed all 12 captures, but the fake device has no acoustic path, so the leak numbers mean nothing there. **This is the one risk left genuinely open.** The design avoids depending on it as far as possible: agent audio only ever plays through the WebRTC remote track, which Chrome's AEC has always used as its reference. Headphones remain the fallback.

## 3. Service worker WebSocket

| Run | Result |
|---|---|
| 30 min, JSON ping every 20 s | 1 connect, 0 disconnects, 1 service worker boot, 89/89 pongs |
| Server sends nothing | Service worker killed 30 s after its last message, every time |
| WebSocket protocol ping frames only | Same as sending nothing |
| Daemon down for 60 s | Service worker died and lost its in-memory queue; reconnected when the alarm woke it |

- **Takeaways:** only data messages reset the idle timer. Recovering from a daemon restart needs a `chrome.alarms` wake (30 s minimum), and state must live in `chrome.storage.session`.
- **One unexplained stall:** an earlier 30-min run stopped logging at 25.7 min, server-side, with no disconnect recorded. The re-run, with a control client and process monitoring added, was clean.
- **Origin check:** the daemon saw `Origin: chrome-extension://<id>`, so the check is feasible. Unpacked IDs change with the load path, so the manifest needs a pinned `key`.

## 4. Picker source resolution

| | Svelte 5.57 | React 18.3 | React 19.3 |
|---|---|---|---|
| 5 targets, exact file:line | 5/5 | 5/5 | 5/5 (element precision) |
| Repeat index/count | ✓ | ✓ | ✓ |
| After an HMR edit | ✓ | ✓ with `getLatestFiber` | ✓ for most elements; a component's root element drops to component precision (right file) |
| Time to resolve | <1 ms | <1 ms | 15–25 ms first pick per module, then 1–2 ms |

The coordinator re-ran the suite independently and got the same results.

- **React 19 precision:** the host fiber's `_debugStack` points at that element's own `jsxDEV` call. bippy's `getSource` maps it back through Vite's source map, so no dev plugin is needed (this answers SPEC §13).
- **HMR on React 19:** React 19 doesn't refresh `_debugStack` when it reuses a fiber. The picker reads the fresh element from the parent's props instead, and detects a stale stack via `?t=`.
- **React DevTools 8 conflict:** bippy 0.7.3 spoofs `hasOwnProperty` for only one call, and DevTools checks twice. When the picker loads first, DevTools never installs. A shim fixes this in both load orders. Worth reporting upstream to bippy.
- **Load order:** the document_start script always ran before React. Fibers are read from DOM nodes anyway, so the early hook only provides the React version.
- **Path normalisation:** paths arrive in three forms: project-relative (Svelte), absolute (React 18) and dev-server URL (React 19). Columns are 0-based for Svelte and React 19, but 1-based for React 18.

## 5. Agent SDK

`claude-agent-sdk` 0.2.164, on the local login (`apiKeySource="/login managed key"`).

| Check | Result |
|---|---|
| acceptEdits edits a file | ✓ |
| `can_use_tool` denies `git push` | ✓ |
| `can_use_tool` pauses `npm install` for approval | ✓, no timeout (a 150 s wait worked) |
| In-process MCP tool | ✓ needs the full `mcp__<server>__<tool>` name and `alwaysLoad`, and `$defs` hoisted to the schema root |
| Image input | ✓ base64 PNG block in the streamed user message |
| Resume | ✓ 0.41 s to reconnect, same session id |
| Interrupt | ✓ 10 ms, session reusable |

Warm first-text latency (median of turns 2+), with the talker config of read-only tools, thinking off and a short system prompt:

| Model | Latency |
|---|---|
| Haiku | 0.54 s |
| Opus 5.5 | 1.11 s |
| Sonnet 5.5 | 1.27 s |

Four gotchas change the design:
- acceptEdits auto-allows some Bash commands without calling `can_use_tool`, and lets out-of-repo writes through if asked. So permissions need a PreToolUse hook (the classifier) plus `can_use_tool` (the approval wait).
- The allow/deny lists alone left about 25 tools available to the talker; it needs `tools=[Read, Grep, Glob]`.
- User settings leak in unless `setting_sources` is pinned.
- Spend for the whole spike was about $1.50.

## 6. Voice loop

The stack is Pipecat 1.12 with Silero VAD, Smart Turn v3, Parakeet TDT 0.6B v3 (parakeet-mlx 0.5.3) and Kokoro-82M (mlx-audio 0.5.8), with a stub LLM.

| Measure | Median | p90 |
|---|---|---|
| End of speech → first output audio (server) | 475 ms | 599 ms |
| Same, at a WebRTC client (aiortc, Opus) | about 800 ms | about 1030 ms |
| VAD stop / Smart Turn verdict / STT done | 222 / 279 / 312 ms | |
| Kokoro first text → first audio | 160 ms | 261 ms |
| User speech onset → bot audio stops (barge-in) | about 345 ms | |

- **Latency budget:** the stack leaves the talker about 0.7 s to its first sentence, within the 1.5 s target. Haiku's warm 0.54 s fits.
- **Word timestamps:** offline median error is 88 ms at 80 ms resolution, but the first word can be up to 0.4 s early, so use end times. Streaming breaks past the context window (0 of 85 words matched on a 34 s stream).
- **Resources:** about 3 GB of unified memory, 3.8 s warm-up from cache, and a first download of about 2.6 GB.
- **Turns end too early:** see the summary row "new". It may partly be an artefact of Kokoro's prosody, so it needs real speech.
- **Kokoro silence:** Kokoro adds about 320 ms of leading silence, which is now trimmed.

## Spec changes made

All of these are in SPEC.md, committed together with this file:

- §5.2: load order rationale, and the React DevTools `hasOwnProperty` shim.
- §5.4: React 19 element precision through `_debugStack`; stale-stack fallback; `getLatestFiber`; Svelte chain details; path and column normalisation; repeat keys.
- §5.5: offscreen reasons; no-grant behaviour and the permission check; agent audio only through the WebRTC remote track.
- §5.6: JSON pings, not protocol pings; alarm wake; `chrome.storage.session`.
- §6.1: pinned manifest `key`.
- §6.3: `tools=[Read, Grep, Glob]`; thinking off; `alwaysLoad`; `$defs` hoisting; `setting_sources` / `verbatim_prompts`.
- §6.5 and §7: two-layer permissions (PreToolUse hook plus `can_use_tool`); out-of-repo writes denied by our classifier.
- §6.6: cancel is `interrupt()`.
- §6.8: offline per-segment STT; Pipecat 1.x wiring; interruption handling; measured latency and talker budget; Kokoro silence trim; resources.
- §6.9: marker placement uses word end times; new turn-continuation rule.
- §9: `ping` and `pong` messages.
- §12: status for each risk, plus the new Smart Turn risk.
- §13: React 19 precision resolved.
