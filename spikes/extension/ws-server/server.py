"""Spike 3 WebSocket server.

Logs every connect / disconnect / message (with timestamps) to stdout and to a
JSONL file, and optionally sends an application-level {"type":"ping"} message
every N seconds. The extension service worker replies with {"type":"pong"}.

Modes (--mode):
  app       application-level JSON ping every --interval s (default; matches SPEC §5.6)
  none      no traffic from the server at all (no app pings, no WS control pings)
  protocol  only WebSocket protocol-level ping frames (websockets' keepalive), no app messages

Usage:
  uv run server.py --port 8765 --mode app --interval 20 --log logs/main.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

START = time.monotonic()
LOG_FILE: Path | None = None
counter = 0


def log(event: str, **fields: Any) -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "t": round(time.monotonic() - START, 3),
        "event": event,
        **fields,
    }
    line = json.dumps(rec)
    print(line, flush=True)
    if LOG_FILE is not None:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")


async def pinger(ws: ServerConnection, conn_id: int, interval: float) -> None:
    seq = 0
    while True:
        await asyncio.sleep(interval)
        seq += 1
        try:
            await ws.send(json.dumps({"type": "ping", "seq": seq, "serverT": time.time()}))
            log("ping_sent", conn=conn_id, seq=seq)
        except ConnectionClosed:
            log("ping_send_failed", conn=conn_id, seq=seq)
            return


async def handler(ws: ServerConnection, mode: str, interval: float) -> None:
    global counter
    counter += 1
    conn_id = counter
    req = ws.request
    log(
        "connect",
        conn=conn_id,
        path=req.path if req else None,
        origin=req.headers.get("Origin") if req else None,
        remote=str(ws.remote_address),
    )
    task = asyncio.create_task(pinger(ws, conn_id, interval)) if mode == "app" else None
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                msg = {"raw": str(raw)}
            log("message", conn=conn_id, msg=msg)
    except ConnectionClosed:
        pass
    finally:
        if task:
            task.cancel()
        log("disconnect", conn=conn_id, code=ws.close_code, reason=ws.close_reason)


async def main() -> None:
    global LOG_FILE
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--mode", choices=["app", "none", "protocol"], default="app")
    p.add_argument("--interval", type=float, default=20.0)
    p.add_argument("--log", type=Path, default=None)
    a = p.parse_args()
    if a.log:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE = a.log
    ping_interval = a.interval if a.mode == "protocol" else None

    async def h(ws: ServerConnection) -> None:
        await handler(ws, a.mode, a.interval)

    async with serve(h, "127.0.0.1", a.port, ping_interval=ping_interval) as server:
        log("server_start", port=a.port, mode=a.mode, interval=a.interval)
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
