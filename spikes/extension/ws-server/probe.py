"""Control client for spike 3: a plain Python WS client that behaves like the SW
(hello + pong). If it keeps ponging while the SW stops, the problem is Chrome-side;
if both stop, the server (or the whole machine) stalled."""

import asyncio
import json
import sys

from websockets.asyncio.client import connect


async def main(port: int) -> None:
    async with connect(f"ws://127.0.0.1:{port}/probe") as ws:
        await ws.send(json.dumps({"type": "hello", "bootId": "python-probe"}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong", "seq": msg["seq"], "bootId": "python-probe"}))


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765))
