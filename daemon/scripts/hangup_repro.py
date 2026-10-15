"""Reproduce the hang-up-after-"On it" bug against the real call talker (costs ~1c).

Asks for a change on a voice turn, closes the reply stream as soon as the first words
arrive (what hanging up does to the pipeline), detaches the call, and checks the worker
got the task. The worker is a stub, so no files change.

    cd daemon && uv run python scripts/hangup_repro.py
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from sidecall.protocol.models import Task
from sidecall.session import OnEvent, RepoSession, WorkerResult
from sidecall.talker import CallTalker

REPO = Path(__file__).resolve().parents[2] / "fixtures" / "svelte5-app"
received: list[Task] = []


class StubWorker:
    def __init__(self, session: RepoSession) -> None: ...

    async def run(self, task: Task, on_event: OnEvent) -> WorkerResult:
        received.append(task)
        return WorkerResult("completed", summary="stub")

    async def interrupt(self) -> None: ...

    async def close(self) -> None: ...


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)
    s = RepoSession(
        REPO,
        Path(tempfile.mkdtemp()),
        lambda _m: None,
        CallTalker,
        StubWorker,
        call_talker_model="haiku",
    )
    s.start()
    call: Any = object()
    s.attach_call(call)
    s.warm_talker()
    await asyncio.sleep(3)
    stream = s.voice_reply("add a dark mode toggle to the header", [], continuation=False)
    first = await anext(stream)
    print(f"heard: {first!r}; hanging up")
    await stream.aclose()
    s.detach_call(call)
    for _ in range(300):
        if received:
            break
        await asyncio.sleep(0.1)
    await s.close()
    print("PASS: worker got", repr(received[0].title) if received else "FAIL: no task")
    return 0 if received else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
