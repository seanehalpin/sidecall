"""2. can_use_tool: deny git push, pause npm install on an approval Future.

Also observes which tool calls reach can_use_tool under acceptEdits
(Edit inside repo, Write outside repo, benign Bash), and demonstrates a
PreToolUse hook alternative.

Usage: uv run python s2_permissions.py [--long-wait SECONDS] [--hook] [--hook-ask] [--probe]
"""

import argparse
import asyncio
import re
import shlex
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from common import CHEAP_MODEL, check_env, drain, log, make_scratch_repo

INSTALL_RE = re.compile(r"\b(npm|pnpm|yarn|bun)\s+(add|install|i)\b")


def classify(tool: str, inp: dict[str, Any]) -> str:
    """Toy version of the SPEC §7 classifier: allow | deny | ask."""
    if tool != "Bash":
        return "allow"
    cmd = inp.get("command", "")
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    joined = " ".join(toks)
    if re.search(r"\bgit\b.*\bpush\b", joined):
        return "deny"
    if INSTALL_RE.search(joined):
        return "ask"
    return "allow"


calls: list[tuple[str, str]] = []
approval_plan: list[bool] = []  # consumed in order by the simulated approver
approval_delay = 2.0


async def simulated_approver(fut: asyncio.Future[bool], what: str) -> None:
    decision = approval_plan.pop(0) if approval_plan else False
    log(f"  approver: card shown for {what!r}; will answer {decision} in {approval_delay}s")
    await asyncio.sleep(approval_delay)
    fut.set_result(decision)


async def can_use_tool(
    tool: str, inp: dict[str, Any], ctx: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    summary = inp.get("command") or inp.get("file_path") or str(inp)[:80]
    calls.append((tool, str(summary)))
    verdict = classify(tool, inp)
    log(f"  can_use_tool({tool}, {summary!r}) -> {verdict}  title={ctx.title!r} reason={ctx.decision_reason!r}")
    if verdict == "deny":
        return PermissionResultDeny(message="git push is blocked by Sidecall. Do not retry.")
    if verdict == "ask":
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        asyncio.create_task(simulated_approver(fut, str(summary)))
        t = asyncio.get_running_loop().time()
        ok = await fut
        log(f"  can_use_tool resumed after {asyncio.get_running_loop().time() - t:.1f}s: approved={ok}")
        if ok:
            return PermissionResultAllow()
        return PermissionResultDeny(
            message="The user declined this install. Do not install it any other way; stop and report."
        )
    return PermissionResultAllow()


async def pre_tool_use_hook(inp: Any, tool_use_id: str | None, ctx: Any) -> dict[str, Any]:
    cmd = inp.get("tool_input", {}).get("command", "")
    log(f"  PreToolUse hook: {inp.get('tool_name')} {cmd!r}")
    if re.search(r"\bgit\b.*\bpush\b", cmd):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "git push is blocked by Sidecall (hook).",
            }
        }
    return {}


async def turn(client: ClaudeSDKClient, prompt: str) -> None:
    log(f">>> {prompt}")
    calls.clear()
    await client.query(prompt)
    await drain(client)
    log(f"<<< can_use_tool calls this turn: {calls}\n")


async def main() -> None:
    global approval_delay
    ap = argparse.ArgumentParser()
    ap.add_argument("--long-wait", type=float, default=0)
    ap.add_argument("--hook", action="store_true", help="run the PreToolUse hook demo only")
    ap.add_argument("--hook-ask", action="store_true", help="PreToolUse 'ask' forwards to can_use_tool?")
    ap.add_argument("--probe", action="store_true", help="which Bash commands bypass can_use_tool")
    args = ap.parse_args()

    check_env()
    repo = make_scratch_repo()
    outside = Path("/tmp/sidecall-outside-test.txt")
    outside.unlink(missing_ok=True)

    opts = ClaudeAgentOptions(
        cwd=repo,
        permission_mode="acceptEdits",
        model=CHEAP_MODEL,
        setting_sources=[],
        can_use_tool=can_use_tool,
        max_turns=8,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": "If a tool call is denied, do not try workarounds; report it in one sentence.",
        },
    )
    if args.hook:
        opts.hooks = {"PreToolUse": [HookMatcher(matcher="Bash", hooks=[pre_tool_use_hook])]}
        opts.permission_mode = "bypassPermissions"  # hook still gates even here
        opts.can_use_tool = None

    if args.hook_ask:

        async def ask_hook(inp: Any, tool_use_id: str | None, ctx: Any) -> dict[str, Any]:
            log(f"  PreToolUse hook -> ask: {inp.get('tool_input', {}).get('command')!r}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "Cautious preset: every shell command needs approval",
                }
            }

        opts.hooks = {"PreToolUse": [HookMatcher(matcher="Bash", hooks=[ask_hook])]}

    async with ClaudeSDKClient(options=opts) as client:
        if args.hook_ask:
            await turn(client, "Run exactly this with Bash, nothing else: ls src")
            return
        if args.hook:
            await turn(client, "Run exactly this with Bash: git push origin main")
            return
        if args.probe:
            for cmd in [
                "git status",
                "mkdir -p tmpdir && touch tmpdir/a.txt",
                "rm index.html",
                "git checkout -- .",
                "git reset --hard",
                "git clean -fd",
                "curl -sI https://example.com",
                "cat /etc/hosts | head -1",
            ]:
                await turn(client, f"Run exactly this with Bash, nothing else: {cmd}")
            return
        if args.long_wait:
            approval_delay = args.long_wait
            approval_plan.append(True)
            await turn(client, "Run exactly this with Bash: npm install is-number --no-audit --no-fund")
            return

        await turn(client, "Run exactly this with Bash: git push origin main")
        approval_plan.append(True)
        await turn(client, "Run exactly this with Bash: npm install is-number --no-audit --no-fund")
        approval_plan.append(False)
        await turn(client, "Run exactly this with Bash: npm install left-pad --no-audit --no-fund")
        await turn(client, "Use the Edit tool to change border-radius in src/card.css to 8px.")
        await turn(client, f"Use the Write tool to create {outside} containing 'hi'.")
        await turn(client, "Run exactly this with Bash: ls src && echo hi > note.txt")

    log("package.json deps now:", (repo / "package.json").read_text())
    log("outside file exists:", outside.exists())


asyncio.run(main())
