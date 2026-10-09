"""6. Read-only talker config, session id + resume, interrupt().

Usage: uv run python s6_talker_resume_interrupt.py [readonly|resume|interrupt ...]
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    ToolPermissionContext,
    create_sdk_mcp_server,
    tool,
)

from common import CHEAP_MODEL, check_env, describe, drain, log, make_scratch_repo


@tool("list_tasks", "List queued worker tasks", {})
async def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "[]"}]}


SERVER = create_sdk_mcp_server(name="sidecall", tools=[list_tasks])
READ_ONLY = ["Read", "Grep", "Glob", "mcp__sidecall__list_tasks"]


async def deny_and_log(tool_name: str, inp: dict[str, Any], ctx: ToolPermissionContext):
    log(f"  can_use_tool asked for {tool_name} -> deny")
    return PermissionResultDeny(message="Talker is read-only.")


async def readonly() -> None:
    for label, extra in [
        # SPEC §6.3 as written: allowed + disallowed lists only.
        ("spec-as-written", dict()),
        # Hardened: also restrict the built-in tool set itself.
        ("hardened", dict(tools=["Read", "Grep", "Glob"])),
    ]:
        repo = make_scratch_repo()
        before = (repo / "src/card.css").read_text()
        log(f"===== readonly {label}")
        opts = ClaudeAgentOptions(
            cwd=repo,
            model=CHEAP_MODEL,
            setting_sources=[],
            mcp_servers={"sidecall": SERVER},
            allowed_tools=READ_ONLY,
            disallowed_tools=["Edit", "Write", "Bash", "NotebookEdit"],
            can_use_tool=deny_and_log,  # catches anything else that would prompt
            max_turns=8,
            **extra,  # type: ignore[arg-type]
        )
        async with ClaudeSDKClient(options=opts) as client:
            await client.query(
                "Change the padding in src/card.css to 99px. Use whatever tool you have to make the "
                "edit happen. Report in one sentence."
            )
            async for msg in client.receive_response():
                if isinstance(msg, SystemMessage) and msg.subtype == "init":
                    log("  tools available to model:", msg.data.get("tools"))
                s = describe(msg)
                if s:
                    log(s)
        after = (repo / "src/card.css").read_text()
        log(f"  file unchanged: {before == after}")


async def resume() -> None:
    repo = make_scratch_repo()
    base: dict[str, Any] = dict(cwd=repo, model=CHEAP_MODEL, setting_sources=[], tools=[], max_turns=1)
    async with ClaudeSDKClient(options=ClaudeAgentOptions(**base)) as c1:
        await c1.query("For our project: the card component should use the brand colour teal-600. Reply 'ok'.")
        r = await drain(c1)
    assert r is not None
    sid = r.session_id
    log(f"session 1 id = {sid}")
    t = time.monotonic()
    c2 = ClaudeSDKClient(options=ClaudeAgentOptions(resume=sid, **base))
    await c2.connect()
    log(f"resume connect: {time.monotonic() - t:.2f}s")
    t = time.monotonic()
    await c2.query("Which brand colour did I say the card should use? Just the colour.")
    r2 = await drain(c2)
    log(f"resumed turn: {time.monotonic() - t:.2f}s; session id after resume = {r2 and r2.session_id} "
        f"(same as before: {r2 and r2.session_id == sid})")
    await c2.disconnect()
    # Same session id resumed from a client whose cwd is a different directory.
    other = make_scratch_repo()
    base["cwd"] = other
    async with ClaudeSDKClient(options=ClaudeAgentOptions(resume=sid, **base)) as c3:
        try:
            await c3.query("Which brand colour did I say the card should use? Just the colour.")
            await drain(c3)
        except Exception as e:  # noqa: BLE001
            log(f"resume from a different cwd raised: {type(e).__name__}: {e}")


async def interrupt() -> None:
    opts = ClaudeAgentOptions(
        cwd=make_scratch_repo(),
        model=CHEAP_MODEL,
        setting_sources=[],
        tools=[],
        thinking={"type": "disabled"},
        include_partial_messages=True,
        max_turns=1,
    )
    async with ClaudeSDKClient(options=opts) as client:
        await client.query("Write the numbers from one to four hundred as English words, comma separated.")
        t0: float | None = None  # time of first text delta
        chars = 0
        t_int = None
        interrupted_task = None
        async for msg in client.receive_response():
            if isinstance(msg, StreamEvent):
                ev = msg.event
                if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    chars += len(ev["delta"]["text"])
                    t0 = t0 or time.monotonic()
                    if t_int is None and time.monotonic() - t0 > 1.0:
                        t_int = time.monotonic()
                        log(f"  calling interrupt() after {chars} streamed chars")
                        interrupted_task = asyncio.create_task(client.interrupt())
                    elif t_int is not None:
                        log(f"  delta after interrupt ({time.monotonic() - t_int:.3f}s): {ev['delta']['text']!r}")
            elif isinstance(msg, ResultMessage):
                now = time.monotonic()
                log(describe(msg))
                if t_int:
                    log(f"  interrupt() -> ResultMessage: {now - t_int:.3f}s")
            else:
                s = describe(msg)
                if s:
                    log(s[:200])
        if interrupted_task:
            await interrupted_task
            log(f"  interrupt() call itself returned; total streamed chars: {chars}")
        # Chars that arrived after interrupt was requested
        t = time.monotonic()
        await client.query("Say 'still alive' and nothing else.")
        await drain(client)
        log(f"  follow-up turn after interrupt took {time.monotonic() - t:.2f}s")


async def main() -> None:
    check_env()
    which = sys.argv[1:] or ["readonly", "resume", "interrupt"]
    for w in which:
        await {"readonly": readonly, "resume": resume, "interrupt": interrupt}[w]()


_ = Path
asyncio.run(main())
