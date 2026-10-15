"""Cost and latency acceptance run, below the audio layer (real Claude, about $0.50).

Three small edits to fixtures/svelte5-app through the real call talker and worker: a
plain spoken turn, an annotated one ([1] = the repeated card title) and one hung up
straight after "On it". Prints talker and worker cost, usage and first-text per turn,
and any rotation. The fixture files really change: undo with `git checkout fixtures/`.

    cd daemon && uv run python scripts/acceptance_session.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from helpers import make_pick

from sidecall.session import RepoSession
from sidecall.talker import CallTalker, ClaudeTalker
from sidecall.worker import ClaudeWorker

REPO = Path(__file__).resolve().parents[2] / "fixtures" / "svelte5-app"
LINES: list[str] = []


class Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.name in ("sidecall.talker", "sidecall.worker", "sidecall.session"):
            LINES.append(f"{record.name.split('.')[-1]}: {msg}")


class StubCall:
    """Stands in for the voice `Call`: picks are passed to the turn directly."""

    def pick_added(self, pick: object) -> None: ...


async def say(s: RepoSession, text: str, picks: list[int], *, hang_up: bool = False) -> str:
    stream = s.voice_reply(text, picks, continuation=False)
    if hang_up:
        first = await anext(stream)
        await stream.aclose()
        return first.strip() + "  [hung up]"
    return "".join([c async for c in stream]).strip()


def table() -> None:
    field = re.compile(r"(first_text|turn_cost|session_cost)=(\S+)")
    usage_re = re.compile(r"usage=((?:\w+:\d+ ?)+|unknown)")
    print("\n| # | who | first_text | turn_cost | input | cache_read | cache_write | output |")
    print("|---|---|---|---|---|---|---|---|")
    n = 0
    for line in LINES:
        who = line.split(":", 1)[0]
        if "turn_cost=" not in line:
            continue
        n += 1
        f = dict(field.findall(line))
        m = usage_re.search(line)
        usage = dict(kv.split(":") for kv in (m.group(1) if m else "").split() if ":" in kv)
        print(
            f"| {n} | {who}{' (no result)' if 'without a result' in line else ''} | "
            f"{f.get('first_text', '-')} | {f.get('turn_cost', '-')} | {usage.get('input', '?')} | "
            f"{usage.get('cache_read', '?')} | {usage.get('cache_write', '?')} | "
            f"{usage.get('output', '?')} |"
        )
    rotations = [ln for ln in LINES if "rotated" in ln or "seeded" in ln]
    print("\nRotation / seeding events:", *(rotations or ["none"]), sep="\n- ")


async def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("sidecall").setLevel(logging.INFO)
    logging.getLogger("sidecall").addHandler(Capture())
    s = RepoSession(
        REPO,
        Path(tempfile.mkdtemp()),
        lambda _m: None,
        ClaudeTalker,
        ClaudeWorker,
        call_talker_factory=CallTalker,
        talker_model="sonnet",
        call_talker_model="haiku",
    )
    s.start()
    call: Any = StubCall()
    s.attach_call(call)
    s.warm_talker()
    await asyncio.sleep(3)

    print("1:", await say(s, "make the Projects heading dark blue", []))
    s.add_pick(
        make_pick(
            1,
            file="src/routes/components/Card.svelte",
            line=8,
            column=3,
            tag="h2",
            classes="card-title",
            repeat=(6, 1),
            chain=[],
        )
    )
    print("2:", await say(s, "make [1] bold and a bit bigger", [1]))
    print("3:", await say(s, "add a thin grey line under the page heading", [], hang_up=True))
    s.detach_call(call)

    for _ in range(600):  # up to 10 min for the worker
        if not s.queue.active and len(s.queue.all_tasks()) >= 3:
            break
        await asyncio.sleep(1)
    tasks = s.queue.all_tasks()
    for t in tasks:
        print(f"task {t.status}: {t.title} — {(t.summary or t.error or '').strip()[:120]}")
    await s.close()
    table()
    ok = len(tasks) == 3 and all(t.status == "completed" for t in tasks)
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
