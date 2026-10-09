"""1. Persistent ClaudeSDKClient on the local login, acceptEdits, edits a file."""

import asyncio
import subprocess

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from common import CHEAP_MODEL, check_env, drain, log, make_scratch_repo


async def main() -> None:
    check_env()
    repo = make_scratch_repo()
    opts = ClaudeAgentOptions(
        cwd=repo,
        permission_mode="acceptEdits",
        model=CHEAP_MODEL,
        setting_sources=[],  # isolate from ~/.claude/settings.json (user has defaultMode=auto)
        max_turns=6,
    )
    async with ClaudeSDKClient(options=opts) as client:
        log("connected")
        await client.query(
            "In src/card.css change the padding to 16px 24px and remove the box-shadow line. "
            "Use the Edit tool. Reply with one short sentence."
        )
        await drain(client)
    log("git diff:\n" + subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True).stdout)


asyncio.run(main())
