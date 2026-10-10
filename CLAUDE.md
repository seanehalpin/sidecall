# CLAUDE.md

## What this is

Sidecall: a Chrome extension plus a local Python daemon for voice- and pointer-driven prototyping, executing through Claude Code. Read `docs/SPEC.md` (design) and `docs/PLAN.md` (milestones) before doing anything.

## How to work

- Work strictly milestone by milestone, in order. Within a milestone, break the work into steps and commit after each working step with a clear message.
- At the end of a milestone:
  - Run all tests.
  - Write `docs/milestones/M<n>.md`: what was built, how to verify it manually, known issues.
  - Stop and wait for the user.
- If an implementation finding contradicts the spec, update SPEC.md in the same commit and call it out in the milestone notes. Don't silently diverge.
- Never `git push`. Never add remotes.

## Verify fast-moving APIs

These libraries change quickly. Check their current docs or source before writing code against them, and don't rely on memory:

- `claude-agent-sdk` (Python): `ClaudeSDKClient`, permission modes, `can_use_tool`, in-process MCP tools, image input, session resume, interrupt
- `pipecat-ai`: SmallWebRTC transport, Silero VAD, Smart Turn v3 analyser, interruptions, custom FrameProcessors and STT/TTS services
- `parakeet-mlx`, `mlx-audio` (Kokoro)
- WXT, Chrome MV3 APIs: `sidePanel`, `offscreen`, MAIN-world content scripts, `tabs.captureVisibleTab`, `notifications`
- `bippy` (React fiber access and source locations)
- Svelte `__svelte_meta` (`loc`, `parent`)

## Stack and conventions

**Daemon** (`/daemon`)
- Python 3.12, uv. Use `ruff` for lint and format, `pyright` (strict on new modules) for types, `pytest` for tests.
- Pydantic models are the source of truth for the protocol. `uv run python -m sidecall.protocol.export` writes JSON Schema to `/protocol`.

**Extension** (`/extension`)
- TypeScript strict, WXT, Svelte 5 with runes. pnpm. vitest and Playwright.
- Generate protocol types from `/protocol`; never hand-edit them.

**Fixtures** (`/fixtures`)
- Minimal apps used as picker and worker targets. Keep them small and realistic: a list, a card, a sidebar, a form.

**Testing scope**
- Keep pure logic in pure functions with unit tests: the permission classifier, marker placement, path normalisation and the queue.

## What you can't verify yourself

Live mic audio, echo cancellation, and the extension running in the user's own Chrome need manual checks. For these, write numbered manual test steps in the milestone notes, with the expected result for each, instead of claiming they work.

## Local run (fill in as built)

- Daemon: `cd daemon && uv run sidecall start` (prints the pairing code; `SIDECALL_HOME=<dir>` isolates state for testing)
- Extension: `cd extension && pnpm build`, then load the unpacked build from `extension/.output/chrome-mv3`. `pnpm dev` watches and writes `extension/.output/chrome-mv3-dev` (WXT's own browser launch is disabled).
- Protocol: after changing `daemon/src/sidecall/protocol/`, run `cd daemon && uv run python -m sidecall.protocol.export`, then `cd extension && pnpm gen:protocol`
- Tests: `cd daemon && uv run pytest` (`--integration` calls Claude); `cd extension && pnpm test && pnpm check && pnpm test:e2e`; picker against the three fixtures (headless, free): `cd extension && pnpm build && node tests/e2e/picker-live.mjs`; acceptance with real Claude, each on a daemon port of its own (`cd extension && pnpm build && node tests/e2e/m1-acceptance.mjs`, about $0.50; `node tests/e2e/m2-acceptance.mjs`, about $0.30)
- Fixtures: `cd fixtures/<app> && pnpm dev`