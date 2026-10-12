# Spike 5 — Agent SDK: findings

`claude-agent-sdk` **0.2.164** (Python 3.12, uv). The SDK uses its bundled CLI (`_bundled/claude` v2.1.292) unless `cli_path` is set. `ANTHROPIC_API_KEY` is unset, and init reports `apiKeySource="/login managed key"`, so it runs on the local Claude Code login. Scratch repos are created in `tempfile.mkdtemp`. Raw output goes to `out/` (not committed). Total spend for the spike was about $1.50.

## How to run

```
cd spikes/agent-sdk && uv sync
uv run python s1_accept_edits.py
uv run python s2_permissions.py [--probe | --hook | --hook-ask | --long-wait 150]
uv run python s3_custom_tool.py
uv run python s4_image.py
uv run python s5_latency.py [haiku default talker-haiku talker-default talker-sonnet]
uv run python s6_talker_resume_interrupt.py [readonly resume interrupt]
```

`SPIKE_MODEL` overrides the cheap model (default `haiku`).

## API names (from installed source)

- `ClaudeSDKClient(options=ClaudeAgentOptions(...))`, used via `connect()` or `async with`, then `query(str | AsyncIterable[dict])` and `receive_response()`. Also `interrupt()`, `set_permission_mode()`, `set_model()`, `rewind_files()`, `disconnect()`.
- Options used: `cwd`, `permission_mode` (default | acceptEdits | plan | bypassPermissions | dontAsk | auto), `model` ("haiku", "sonnet", or unset = opus-5-5), `tools`, `allowed_tools`, `disallowed_tools`, `can_use_tool`, `hooks`, `mcp_servers`, `include_partial_messages`, `resume`, `setting_sources`, `system_prompt`, `thinking`, `env`, `max_turns`.
- `can_use_tool(name, input, ToolPermissionContext)` returns `PermissionResultAllow(updated_input=…)` or `PermissionResultDeny(message, interrupt=False)`. The context carries `decision_reason`, `blocked_path`, `tool_use_id` and `suggestions`.
- `HookMatcher(matcher="Bash", hooks=[fn], timeout=None)`. A hook returns `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow" | "deny" | "ask" | "defer", "permissionDecisionReason": …}}`.
- `@tool(name, desc, schema)` and `create_sdk_mcp_server(name, version, tools)`. The schema can be a dict of types or full JSON Schema (passed verbatim when it has `type` and `properties`).
- `StreamEvent.event` is the raw API event. Use `content_block_delta` / `text_delta` for first-token timing.

## 1. acceptEdits: works

The agent ran Read then Edit on `src/card.css`, and `git diff` shows `padding 16px 24px` and the shadow removed. `can_use_tool` was not consulted. The cold first connect took 3.6 s (first launch of the bundled CLI).

## 2. can_use_tool: works, with caveats

- **Where it fires under acceptEdits:**
  - `git push`: denied. The model stopped, and `permission_denials` lists the call.
  - `npm install`: the approval Future resolved after 2 s. Allow: it installed. Deny: it abandoned the step.
  - `git checkout -- .`, `git reset --hard`, `git clean -fd`, `curl`, `cat /etc/hosts`.
  - Write to `/tmp`, with reason "Path is outside allowed working directories". **It succeeds if we allow it.**
- **Where it doesn't fire:** in-repo Edit, `ls`, `git status`, `mkdir`/`touch`, `echo > file`. acceptEdits auto-allows these.
- **No timeout:** a 150 s approval wait worked. Hooks, by contrast, time out after 60 s by default.
- **Streaming mode:** it needs the control protocol. `ClaudeSDKClient` is always in streaming mode, and the SDK sets `permission_prompt_tool_name="stdio"` itself.
- **What shadows it:** `allowed_tools` (with a `CanUseToolShadowedWarning`), `permission_mode`, and allow rules in settings files.
- **User settings:** a personal `defaultMode` (for example `auto`) would skew permission results, so all spike runs use `setting_sources=[]`.
- **PreToolUse hook:** fires for every call. "deny" blocked push even under bypassPermissions. "ask" forwarded the call to `can_use_tool` with our reason.

## 3. Custom tool: works

`mcp__sidecall__dispatch_task` was called with a Brief-shaped argument, parsed in-process by Pydantic, and returned task id `t-001`.

- `allowed_tools` needs the full `mcp__<server>__<tool>` name. The SDK docstring example is misleading here.
- If the tool isn't in `allowed_tools`, `can_use_tool` is consulted for it.
- MCP tools sit behind ToolSearch by default, which adds an extra 2–3 s round-trip. Fix it with `"alwaysLoad": True` in the server dict, or `env={"ENABLE_TOOL_SEARCH": "false"}`.
- A nested Pydantic schema breaks its `$defs` refs: the CLI rejects the call with `PointerToNowhere` and the handler is never called. Hoist `$defs` to the root of the tool schema.

## 4. Image input: works

An `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": …}}` block in a streamed user message was answered correctly ("red, 42") in 1.7 s. The image stayed in context for the next turn.

## 5. Latency

Seconds to the first text delta (`include_partial_messages`). "Turns 2+" is the median of 5 turns on one persistent client.

| Config | Model | Connect | Turn 1 | Turns 2+ (range) | Turns 2+ to Result |
|---|---|---|---|---|---|
| Full preset | haiku | 0.67 | 1.41 | 0.92 (0.80–0.99) | 0.99 |
| Full preset | opus-5-5 | 0.30 | 1.66 | 1.20 (1.13–1.45) | 1.88 |
| Talker config | haiku | 0.30 | 0.73 | **0.54** (0.53–0.59) | 0.58 |
| Talker config | sonnet-5-5 | 1.53 | 1.14 | 1.27 (0.70–1.30) | 1.30 |
| Talker config | opus-5-5 | 0.30 | 1.29 | 1.11 (1.06–1.32) | 1.75 |

- "Talker config" means `tools=[Read, Grep, Glob]`, a short custom system prompt, and thinking disabled.
- The lean config also cuts cost: $0.003 vs $0.020 per haiku turn.
- Deltas start streaming about 0.6 s before the full AssistantMessage, so TTS can start from the stream events.
- Disconnect takes about 0.75 s.

## 6. Read-only talker, resume, interrupt

- **Read-only:** using only the allowed/disallowed lists, the file stayed unchanged, but about 25 tools remained available (Agent, WebFetch, Skill, Cron, …). Haiku spawned a subagent that searched for 60 s ($0.15). With `tools=[Read, Grep, Glob]` it declined in one turn.
- **Resume:** take `ResultMessage.session_id`, then create a new client with `resume=sid`. It connected in 0.41 s, recalled the fact ("teal-600"), and kept the same session id. Resuming from a different cwd also worked.
- **Interrupt:** calling `interrupt()` after about 1 s of streaming yields `ResultMessage(error_during_execution, terminal_reason="aborted_streaming")` 10 ms later. No deltas arrive after that, and the client is reusable: the next turn took 0.59 s.

## Gotchas

- The default `setting_sources` loads user settings, which can change permission behaviour.
- Use `verbatim_prompts=True` to stop `@path` and `/slash` expansion in assembled prompts.
- Ignore SystemMessage subtypes `thinking_tokens`, `status` and `task_*`.
- `error_max_turns` is reported via `terminal_reason`.
- `enable_file_checkpointing` plus `rewind_files()` exists (untested).
- One stray "Safe-chain" stderr line came from the local shell wrapper.

## Recommended SPEC changes

1. **§6.3 Read-only:** "Built-in tools are restricted via `tools=["Read","Grep","Glob"]`. `allowed_tools` holds those plus the full `mcp__sidecall__*` names. `disallowed_tools` holds Edit, Write, Bash and NotebookEdit."
2. **§6.3 Latency:** "Thinking is disabled, with a custom spoken-style system prompt. The SDK MCP server is registered with `alwaysLoad: true`. Tool schemas are generated from Pydantic with `$defs` hoisted to the root."
3. **§6.5/§7 Two-layer enforcement:** "A PreToolUse hook (`Bash|Edit|Write|NotebookEdit`) runs the classifier on every call. It denies git push and out-of-repo writes, and returns 'ask' for approvals. `can_use_tool` then awaits the approval Future, which has no timeout. Never wait inside the hook, because hooks time out after 60 s. Both layers are needed because acceptEdits auto-allows some Bash."
4. **§7:** "Out-of-repo writes must be denied by our classifier; acceptEdits does not block them."
5. **§6.3/§6.5:** "Both clients use `setting_sources=["project"]` and `verbatim_prompts=True`."
6. **§6.6:** "Cancelling the running task calls `interrupt()`: about 10 ms, `terminal_reason` aborted_*, and the session stays reusable."
7. **§12.5:** all four sub-risks resolved.
8. **§6.5 Undo (optional):** consider file checkpointing with `rewind_files()`.
