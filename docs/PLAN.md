# Sidecall — Build plan

Work one milestone at a time. Stop at the end of each milestone for manual verification by the user. Each milestone lists acceptance criteria; it is done only when all of them pass.

## Milestone 0 — Spikes

Throwaway code in `/spikes/<name>`. Goal: answer the risks in SPEC §12 before building on them. Write findings to `/docs/spikes.md`: what works, what doesn't, and any spec changes needed.

1. **Mic in the extension.** A minimal MV3 extension with a side panel, an offscreen document (`USER_MEDIA`), and an options page that grants mic permission. Record 5 s from the offscreen document and play it back.
2. **Echo cancellation.** Play TTS audio from the offscreen document while capturing the mic, without headphones. Check whether the agent's voice leaks into the capture.
3. **Service worker WebSocket.** Keep a WebSocket to a local server open for 30 minutes with 20 s pings. Log any disconnects.
4. **Picker source resolution.** Create the three fixture apps:
   - `fixtures/svelte5-app`: SvelteKit, Svelte ≥ 5.35.1
   - `fixtures/react18-app`: Vite
   - `fixtures/react19-app`: Vite
   
   A MAIN-world script at document_start logs the source location and component chain for a clicked element in each app. Test with and without the React DevTools extension installed.
5. **Agent SDK.** From Python:
   - Run a persistent `ClaudeSDKClient` on the local Claude Code login with `acceptEdits`.
   - A `can_use_tool` callback blocks `git push` and pauses on `npm install` for approval.
   - An in-process custom tool is callable.
   - Image input works.
   - Measure first-token latency on the 2nd and later turns.
6. **Voice loop.** A Pipecat pipeline on the Mac running Parakeet (with word timestamps), Silero, Smart Turn v3 and Kokoro, with a stub LLM. Use a local browser page as the client. Measure end-of-speech to first audio.

**Acceptance:** `/docs/spikes.md` answers each risk with evidence, and SPEC.md is updated where findings change the design.

## Milestone 1 — Daemon core and text rail

- **Daemon:** server, pairing, origin-to-repo registry, state directory, protocol models with JSON Schema export.
- **Talker** (text only): read-only tools plus `dispatch_task`, `list_tasks`, `cancel_task`.
- **Worker:** brief → prompt, event streaming, completed and failed messages.
- **Queue:** first in, first out, with persistence.
- **Permission classifier and approval flow.**
- **Extension:** WXT scaffold, generated protocol types, service worker WebSocket, rail with header, transcript, composer, task queue and approval cards, plus notifications.

**Acceptance**
- After pairing, typing "make the heading on the home page red" in the rail on `fixtures/svelte5-app` produces a confirmed brief, a dispatched task, an edited file, a hot-reloaded page and a completion message in the rail.
- An install request shows an approval card. Deny cancels the step; allow proceeds.
- `git push` is refused.
- The queue survives a daemon restart.
- Unit tests pass for the classifier, queue and protocol.

## Milestone 2 — Picker

- ⌥ hover, click, drag, ↑ / ↓ traversal and Esc, in a shadow-DOM overlay.
- Full pick payload (SPEC §5.3), with screenshots uploaded to the daemon.
- Source resolution for Svelte 5 and React 18/19 via bippy, plus daemon-side path normalisation.
- Repeat detection.
- Pins and chips: inline chips in the composer and transcript, hover-to-highlight.
- Re-anchoring after HMR.
- Picks included in talker turns and worker prompts.

**Acceptance**
- In each fixture app, picking an element shows the correct file:line (Svelte and React 18) or the correct component file (React 19) in the chip tooltip.
- "make [1] match [2]", typed with two picks, produces a correct edit.
- A pick on a list row reports the repeat count, and the edit lands in the template.
- Pins survive an HMR update.
- Playwright tests pass against all three fixtures.

## Milestone 3 — Voice

- Offscreen-document call: mic, WebRTC, playback.
- Pipecat pipeline (SPEC §6.8) with a TalkerProcessor wrapping the talker session, and interruptions.
- Live transcript in the rail (partial and final).
- Turn assembly with pick binding and marker placement (SPEC §6.9).
- Announcer: speak at pauses, batching, notification callbacks when no call is active.

**Acceptance**
- An open-mic conversation on a fixture app: interrupting the agent mid-sentence stops it within 300 ms.
- ⌥-clicking two elements while saying "make this match that" produces a transcript with `[1]` and `[2]` in the right places.
- A task completing mid-conversation is announced only after you pause.
- Hanging up, then letting a task finish, produces a notification. Clicking it starts a call that opens with the summary.
- Median end-of-speech to first audio is ≤ 1.5 s.

## Milestone 4 — Settings and persistence

- Autonomy presets in settings.
- `notes.md` loaded at call start; the talker proposes additions.
- Session resume across daemon restarts.
- Transcript history per repo.
- `sidecall` CLI: `start`, `pair`, `status`, `repos`.

**Acceptance**
- After a restart, "do the same thing on the detail page" works, referring to yesterday's task.
- Switching presets changes the approval behaviour immediately.