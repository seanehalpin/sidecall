"""Announcer: which task events get spoken, when, and how they are batched (SPEC §6.7).

Pure: the session feeds it events and the call asks `announcement_due` on a timer.

- Priority: blocked (approval needed) > failed > completed. Progress is never spoken.
- During a call, announce only once the user has been quiet for QUIET_MS and the agent
  isn't speaking or thinking; then one short spoken update covers every pending event.
- Events the talker has already seen in a turn's system notes are dropped (`mark_seen`),
  so they aren't repeated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

AnnounceKind = Literal["blocked", "failed", "completed"]

PRIORITY: dict[AnnounceKind, int] = {"blocked": 0, "failed": 1, "completed": 2}
QUIET_MS = 1200.0
"""The user must have been silent this long before the talker speaks an update."""


@dataclass(frozen=True)
class Announcement:
    kind: AnnounceKind
    task_id: str
    title: str
    detail: str = ""
    at: float = 0.0


class Announcer:
    """Pending announcements, at most one per task (the latest event wins)."""

    def __init__(self) -> None:
        self._pending: dict[str, Announcement] = {}

    def add(self, event: Announcement) -> None:
        self._pending.pop(event.task_id, None)
        self._pending[event.task_id] = event

    def resolve_blocked(self, task_id: str) -> None:
        """The approval was answered: no need to ask for it any more."""
        event = self._pending.get(task_id)
        if event is not None and event.kind == "blocked":
            del self._pending[task_id]

    def mark_seen(self) -> None:
        """A talker turn carried the task notes: completed and failed are known."""
        for task_id, event in list(self._pending.items()):
            if event.kind != "blocked":
                del self._pending[task_id]

    def pending(self) -> list[Announcement]:
        return sorted(self._pending.values(), key=lambda e: (PRIORITY[e.kind], e.at))

    def take(self) -> list[Announcement]:
        out = self.pending()
        self._pending.clear()
        return out

    def __bool__(self) -> bool:
        return bool(self._pending)


def announcement_due(
    now: float,
    *,
    pending: bool,
    user_speaking: bool,
    agent_busy: bool,
    last_user_activity: float | None,
) -> bool:
    """Whether to speak pending announcements now (during a call)."""
    if not pending or user_speaking or agent_busy:
        return False
    return last_user_activity is None or now - last_user_activity >= QUIET_MS


_NUMBERS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _count(n: int) -> str:
    return _NUMBERS[n] if n < len(_NUMBERS) else str(n)


def batch_phrase(events: Sequence[Announcement]) -> str:
    """A spoken-style summary of the events, highest priority first, e.g.
    "One needs your OK, and two things finished." The talker phrases the final words;
    this is its model sentence."""
    if not events:
        return ""
    if len(events) == 1:
        e = events[0]
        if e.kind == "blocked":
            return f"{e.title} needs your OK."
        if e.kind == "failed":
            return f"{e.title} failed."
        return f"{e.title} is done."
    counts = {k: sum(1 for e in events if e.kind == k) for k in PRIORITY}
    parts: list[str] = []
    if counts["blocked"]:
        n = counts["blocked"]
        parts.append(f"{_count(n)} {'needs' if n == 1 else 'need'} your OK")
    if counts["failed"]:
        n = counts["failed"]
        parts.append(f"{_count(n)} failed" if parts else f"{_count(n)} {_thing(n)} failed")
    if counts["completed"]:
        n = counts["completed"]
        parts.append(f"{_count(n)} {_thing(n)} finished")
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:] + "."


def _thing(n: int) -> str:
    return "thing" if n == 1 else "things"


def describe(event: Announcement) -> str:
    """One system line per event, for the talker's announcement prompt."""
    detail = f": {event.detail}" if event.detail else ""
    if event.kind == "blocked":
        return f"[System] Task '{event.title}' is waiting for the user's OK in the panel{detail}"
    if event.kind == "failed":
        return f"[System] Task '{event.title}' failed{detail}"
    return f"[System] Task '{event.title}' completed{detail}"


def render_announcement(
    events: Sequence[Announcement], notes: Sequence[str] = (), *, opening: bool = False
) -> str:
    """The talker prompt for a spoken update: no user text, just the events.

    `notes` are the unseen task notes the turn also carries (they already describe
    completed and failed tasks, so those events aren't repeated from `events`)."""
    lines = [*notes]
    noted = " ".join(notes)
    for e in events:
        if e.kind == "blocked" or f"'{e.title}'" not in noted:
            lines.append(describe(e))
    when = (
        "The user just started a voice call."
        if opening
        else "The user is in a voice call and has paused."
    )
    in_flight = " Then say what's still in progress, from the [Worker] line." if opening else ""
    if events:
        update = (
            "give them a spoken update about the tasks above in one short sentence, most "
            f'urgent first, and batch them, like "{batch_phrase(events)}" Use the task names '
            f"naturally. If something failed or needs their OK, say so.{in_flight}"
        )
    else:
        update = "tell them in one short sentence what's still in progress, from the [Worker] line."
    lines.append(
        f"[System] {when} Nobody asked anything: {update} Don't call any tools and don't ask "
        "a question unless something needs their OK."
    )
    return "\n".join(lines)
