"""Worker session rotation and the task log that carries context across sessions.

A worker session is resumed while the user works in a burst, so the prompt cache stays
warm. It's replaced when resuming stops paying: after an idle gap (the cache is cold, so
resuming means writing the whole history to cache again), or once its context or cost
gets large. The new session is seeded from the task log, which the daemon writes from
each worker result (no LLM involved), plus the repo's git state. Pure except `TaskLog`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SEED_LOG_ENTRIES = 20


@dataclass(frozen=True)
class RotationPolicy:
    idle_secs: float = 15 * 60
    max_context_tokens: int = 120_000
    max_session_cost: float = 15.0


@dataclass(frozen=True)
class WorkerSessionStats:
    """What's known about the current worker session (persisted with its id)."""

    last_result_at: float | None = None
    """Wall-clock seconds of its last result."""
    context_tokens: int | None = None
    """Tokens its last request read: input + cache read + cache write."""
    session_cost: float = 0.0
    """Summed turn costs, across daemon restarts (the SDK's total restarts with the CLI)."""


def rotation_reason(policy: RotationPolicy, stats: WorkerSessionStats, now: float) -> str | None:
    """Why the session should be replaced before the next task, or None to resume it."""
    if stats.last_result_at is not None and now - stats.last_result_at > policy.idle_secs:
        return f"idle {int((now - stats.last_result_at) // 60)} min"
    if stats.context_tokens is not None and stats.context_tokens > policy.max_context_tokens:
        return f"context {stats.context_tokens} tokens"
    if stats.session_cost > policy.max_session_cost:
        return f"session cost ${stats.session_cost:.2f}"
    return None


@dataclass(frozen=True)
class TaskLogEntry:
    ts: float
    title: str
    request: str
    """The brief's goal, plus the user's words when there were any."""
    status: str
    outcome: str
    files: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "title": self.title,
            "request": self.request,
            "status": self.status,
            "outcome": self.outcome,
            "files": self.files,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TaskLogEntry:
        return cls(
            ts=float(data.get("ts", 0)),
            title=str(data.get("title", "")),
            request=str(data.get("request", "")),
            status=str(data.get("status", "")),
            outcome=str(data.get("outcome", "")),
            files=[str(f) for f in data.get("files", [])],
        )


class TaskLog:
    """`task-log.jsonl` in the repo's state directory, one entry per finished task."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: TaskLogEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_json()) + "\n")

    def recent(self, n: int = SEED_LOG_ENTRIES) -> list[TaskLogEntry]:
        if not self.path.exists():
            return []
        out: list[TaskLogEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines()[-n:]:
            try:
                out.append(TaskLogEntry.from_json(json.loads(line)))
            except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
                log.warning("skipping a bad line in %s", self.path)
        return out


def _clip(text: str, limit: int) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def render_seed(entries: list[TaskLogEntry], git_status: str, git_log: str) -> str:
    """Context for a fresh worker session, put ahead of its first task. The repo's
    CLAUDE.md isn't repeated: the worker loads project settings, which include it."""
    lines = [
        "# Context from earlier sessions",
        "",
        "This is a fresh session. Earlier tasks in this repo, oldest first (from On it's "
        "task log, not your memory):",
    ]
    if entries:
        for e in entries:
            files = f" Files: {', '.join(e.files)}." if e.files else ""
            lines.append(
                f'- [{e.status}] {e.title}: asked "{_clip(e.request, 200)}". '
                f"Outcome: {_clip(e.outcome, 200) or 'none'}.{files}"
            )
    else:
        lines.append("- (none yet)")
    lines += ["", "git status --short:", git_status.rstrip() or "(clean)", ""]
    lines += ["git log --oneline -10:", git_log.rstrip() or "(no commits)", "", "---", "", ""]
    return "\n".join(lines)
