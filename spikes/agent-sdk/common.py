"""Shared helpers for the agent-sdk spike scripts."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

CHEAP_MODEL = os.environ.get("SPIKE_MODEL", "haiku")

T0 = time.monotonic()


def log(*args: Any) -> None:
    print(f"[{time.monotonic() - T0:7.2f}s]", *args, flush=True)


def check_env() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    log("ANTHROPIC_API_KEY set:", bool(key), "(spike expects False -> local Claude Code login)")


def make_scratch_repo() -> Path:
    """A tiny throwaway git repo in a temp dir (never inside the project tree)."""
    d = Path(tempfile.mkdtemp(prefix="sidecall-scratch-"))
    (d / "src").mkdir()
    (d / "package.json").write_text(
        '{\n  "name": "scratch",\n  "version": "0.0.0",\n  "private": true\n}\n'
    )
    (d / "index.html").write_text(
        '<!doctype html>\n<html><head><link rel="stylesheet" href="src/card.css"></head>\n'
        '<body><div class="card"><h3>Title</h3><p>Body</p></div></body></html>\n'
    )
    (d / "src" / "card.css").write_text(
        ".card {\n  padding: 12px 16px;\n  border-radius: 4px;\n"
        "  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);\n}\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    subprocess.run(
        ["git", "-c", "user.email=s@x", "-c", "user.name=s", "commit", "-qm", "init"],
        cwd=d,
        check=True,
    )
    log("scratch repo:", d)
    return d


def describe(msg: Any) -> str | None:
    """One-line summary of an SDK message (None to skip)."""
    if isinstance(msg, StreamEvent):
        return None
    if isinstance(msg, AssistantMessage):
        parts: list[str] = []
        for b in msg.content:
            if isinstance(b, TextBlock):
                parts.append(f"text={b.text[:160]!r}")
            elif isinstance(b, ToolUseBlock):
                parts.append(f"tool_use {b.name} {str(b.input)[:160]}")
            else:
                parts.append(type(b).__name__)
        return f"Assistant[{msg.model}] " + " | ".join(parts)
    if isinstance(msg, UserMessage):
        if isinstance(msg.content, list):
            parts = []
            for b in msg.content:
                if isinstance(b, ToolResultBlock):
                    c = b.content if isinstance(b.content, str) else str(b.content)
                    parts.append(f"tool_result err={b.is_error} {c[:160]!r}")
            return "User " + " | ".join(parts) if parts else None
        return None
    if isinstance(msg, SystemMessage):
        if msg.subtype == "init":
            d = msg.data
            return (
                f"System init model={d.get('model')} permissionMode={d.get('permissionMode')} "
                f"apiKeySource={d.get('apiKeySource')} session={d.get('session_id')}"
            )
        return f"System {msg.subtype}"
    if isinstance(msg, ResultMessage):
        return (
            f"Result subtype={msg.subtype} is_error={msg.is_error} turns={msg.num_turns} "
            f"duration_ms={msg.duration_ms} cost=${msg.total_cost_usd} "
            f"terminal={msg.terminal_reason} denials={msg.permission_denials} "
            f"result={(msg.result or '')[:200]!r}"
        )
    return type(msg).__name__


async def drain(client: Any) -> ResultMessage | None:
    """Print every message of the current response; return the ResultMessage."""
    result: ResultMessage | None = None
    async for msg in client.receive_response():
        s = describe(msg)
        if s:
            log(s)
        if isinstance(msg, ResultMessage):
            result = msg
    return result
