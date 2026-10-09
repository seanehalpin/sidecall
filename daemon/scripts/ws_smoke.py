"""End-to-end smoke test against a running daemon, without the extension.

    SIDECALL_HOME=$(mktemp -d) uv run sidecall start          # terminal 1, note the code
    uv run python scripts/ws_smoke.py <pairing-code>           # terminal 2

Pairs, says hello for http://localhost:5173, maps it to a fresh COPY of
fixtures/svelte5-app in a temp dir (never the tracked fixture), sends one user.text
and prints every message until the task completes or fails. Approvals are denied
unless --approve is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Origin

EXT_ORIGIN = "chrome-extension://kcjkimilmanlonlplfancfpoekkphjhb"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "svelte5-app"
T0 = time.monotonic()


def log(*parts: object) -> None:
    print(f"[{time.monotonic() - T0:6.2f}s]", *parts, flush=True)


def copy_fixture() -> Path:
    dest = Path(tempfile.mkdtemp(prefix="sidecall-smoke-")) / "svelte5-app"
    shutil.copytree(FIXTURE, dest, ignore=shutil.ignore_patterns("node_modules", ".svelte-kit"))
    git = ["git", "-c", "user.email=smoke@x", "-c", "user.name=smoke"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dest, check=True)
    subprocess.run(["git", "add", "."], cwd=dest, check=True)
    subprocess.run([*git, "commit", "-qm", "fixture copy"], cwd=dest, check=True)
    return dest


def git_diff(repo: Path) -> str:
    proc = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True, check=False)
    return proc.stdout


async def send(ws: ClientConnection, mtype: str, payload: dict[str, Any]) -> None:
    msg = {"type": mtype, "id": uuid.uuid4().hex[:8], "ts": time.time() * 1000, "payload": payload}
    await ws.send(json.dumps(msg))


async def recv(ws: ClientConnection) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(await ws.recv())
    return data


async def run(args: argparse.Namespace) -> int:
    repo = Path(args.repo) if args.repo else copy_fixture()
    log("repo copy:", repo)
    url = f"ws://127.0.0.1:{args.port}/ws"
    async with connect(url, origin=Origin(EXT_ORIGIN)) as ws:
        await send(ws, "pair.request", {"code": args.code})
        msg = await recv(ws)
        if msg["type"] != "pair.result":
            log("pairing failed:", msg)
            return 1
        token = msg["payload"]["token"]
        log("paired")
        await send(
            ws,
            "hello",
            {
                "token": token,
                "extVersion": "smoke",
                "origin": args.origin,
                "clockSample": time.time() * 1000,
            },
        )
        msg = await recv(ws)
        log("<-", msg["type"], json.dumps(msg["payload"])[:200])
        if msg["type"] == "repo.unknown":
            await send(ws, "repo.map", {"origin": args.origin, "path": str(repo)})
            msg = await recv(ws)
            log("<-", msg["type"], json.dumps(msg["payload"])[:200])
        if msg["type"] != "welcome":
            return 1

        await send(ws, "user.text", {"text": args.text, "pickIds": []})
        log("-> user.text", args.text)
        reply: list[str] = []
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            msg = await asyncio.wait_for(recv(ws), deadline - time.monotonic())
            mtype, payload = msg["type"], msg["payload"]
            if mtype == "ping":
                await send(ws, "pong", {"seq": payload["seq"]})
                continue
            if mtype == "agent.text.delta":
                reply.append(payload["delta"])
                continue
            if mtype == "agent.text.done":
                log("<- agent:", "".join(reply))
                reply.clear()
                continue
            if mtype == "queue.state":
                tasks = ", ".join(f"{t['title']}={t['status']}" for t in payload["tasks"])
                log("<- queue.state", tasks)
                continue
            log("<-", mtype, json.dumps(payload)[:300])
            if mtype == "task.blocked":
                approval = payload["approval"]
                await send(ws, "approval.respond", {"id": approval["id"], "allow": args.approve})
            if mtype in ("task.completed", "task.failed"):
                # Drain the trailing system transcript + notify briefly.
                try:
                    while True:
                        extra = await asyncio.wait_for(recv(ws), 1.0)
                        if extra["type"] not in ("ping", "queue.state"):
                            log("<-", extra["type"], json.dumps(extra["payload"])[:300])
                except TimeoutError:
                    pass
                log("git diff:\n" + await asyncio.to_thread(git_diff, repo))
                return 0 if mtype == "task.completed" else 2
        log("timed out")
        return 3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", help="pairing code printed by `sidecall start`")
    ap.add_argument("--port", type=int, default=7823)
    ap.add_argument("--origin", default="http://localhost:5173")
    ap.add_argument("--repo", help="map to this path instead of a fresh fixture copy")
    ap.add_argument("--text", default="make the heading on the home page red")
    ap.add_argument("--approve", action="store_true", help="allow approval requests")
    ap.add_argument("--timeout", type=float, default=300)
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
