"""`sidecall` command line: `sidecall start [--port N] [--preload-voice]`."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .config import Config
from .server import Daemon, create_app


def _banner(cfg: Config, port: int, code: str) -> str:
    return "\n".join(
        [
            "",
            f"  Sidecall daemon listening on ws://127.0.0.1:{port}/ws",
            f"  Config: {cfg.path}",
            f"  Extension ID: {cfg.extension_id}",
            "",
            f"  Pairing code: {code}   (valid for 10 minutes)",
            "  Enter this code in the Sidecall extension.",
            "",
        ]
    )


def start(port: int | None, verbose: bool, preload_voice: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    for noisy in ("uvicorn.access", "httpx", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = Config.load()
    port = port or cfg.port
    daemon = Daemon(cfg)
    code = daemon.pairing.issue()
    print(_banner(cfg, port, code), flush=True)
    uvicorn.run(
        create_app(daemon, preload_voice=preload_voice),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        ws_max_size=32 * 1024 * 1024,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sidecall")
    sub = parser.add_subparsers(dest="command", required=True)
    p_start = sub.add_parser("start", help="run the daemon")
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("-v", "--verbose", action="store_true")
    p_start.add_argument(
        "--preload-voice",
        action="store_true",
        help="load and warm the voice models at startup instead of on the first call",
    )
    args = parser.parse_args(argv)
    if args.command == "start":
        start(args.port, args.verbose, args.preload_voice)
