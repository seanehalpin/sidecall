"""Turn assembly, pick binding and turn continuation (SPEC §6.9). Pure: no Pipecat, no I/O.

All times are daemon-clock milliseconds (`time.time() * 1000`), the clock pick
`createdAt` values are already in (the extension converts with its clock offset).
STT word times arrive as `time.monotonic()` seconds and are converted at the pipeline
edge (`monotonic_to_daemon_ms`).

- `assemble_turn`: words from every STT segment of a turn + the picks in the store →
  text with `[n]` markers and the turn's pick ids.
- `can_continue` / `pick_hold_until`: the two turn-continuation rules.
- `TurnTracker`: the small state machine that applies them across a call.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

PRE_SPEECH_MS = 3000.0
"""Picks up to this long before speech starts bind to the turn."""
CONTINUATION_GAP_MS = 1500.0
"""Resuming within this long of a turn end can continue (merge into) that turn."""
CONTINUATION_MAX_SPOKEN_WORDS = 3.0
"""...as long as the agent hasn't said more than this many words of its reply."""
SPOKEN_WORDS_PER_SEC = 2.7
"""Kokoro's speaking rate, to estimate how much of a reply the user has heard."""
PICK_HOLD_MS = 1000.0
TURN_DEBOUNCE_MS = float(os.environ.get("SIDECALL_TURN_DEBOUNCE_MS", "500"))
"""Every turn waits this long before the talker is asked: Smart Turn sometimes ends a turn
mid-sentence, and speech in the window continues the turn without an API call. Speech is
only reported after VAD_START_SECS (0.2 s) of it, so the window catches restarts within
about 300 ms."""
"""Don't submit a turn on Smart Turn alone within this long after a pick."""
LATE_PICK_MS = 1000.0
"""A pick made up to this long after a turn ended still belongs to it (the daemon only
learns of a pick after its screenshot upload, so it can't hold the turn for it)."""


def monotonic_to_daemon_ms(t: float, *, offset_s: float | None = None) -> float:
    """A `time.monotonic()` time in daemon-clock ms. `offset_s` is `time() - monotonic()`."""
    if offset_s is None:
        offset_s = time.time() - time.monotonic()
    return (t + offset_s) * 1000.0


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Segment:
    """One STT segment (one VAD segment) of a turn."""

    text: str
    words: tuple[TimedWord, ...] = ()
    start: float | None = None
    """When the segment's speech started (daemon ms), if known."""

    @property
    def speech_start(self) -> float | None:
        starts = [w.start for w in self.words[:1]]
        if self.start is not None:
            starts.append(self.start)
        return min(starts) if starts else None


@dataclass(frozen=True)
class PickTime:
    id: int
    created_at: float


@dataclass(frozen=True)
class AssembledTurn:
    text: str
    pick_ids: tuple[int, ...]
    window: tuple[float, float]


_TRAILING_PUNCT = re.compile(r"^(.*?)([.,!?;:…]*)$", re.DOTALL)


def _attach(word: str, markers: Sequence[int]) -> str:
    """`word` with markers after its core, before trailing punctuation: "that [2]."."""
    tags = " ".join(f"[{i}]" for i in markers)
    m = _TRAILING_PUNCT.match(word)
    core, punct = (m.group(1), m.group(2)) if m else (word, "")
    if not core:  # a bare punctuation token
        return f"{word} {tags}"
    return f"{core} {tags}{punct}"


def turn_window(segments: Sequence[Segment], end: float) -> tuple[float, float]:
    """From PRE_SPEECH_MS before speech starts until the turn ends."""
    starts = [s for seg in segments if (s := seg.speech_start) is not None]
    speech_start = min(starts) if starts else end
    return (speech_start - PRE_SPEECH_MS, end)


def bind_picks(picks: Iterable[PickTime], window: tuple[float, float]) -> list[PickTime]:
    """Picks created inside the window, in creation order."""
    lo, hi = window
    inside = [p for p in picks if lo <= p.created_at <= hi]
    return sorted(inside, key=lambda p: (p.created_at, p.id))


def place_markers(words: Sequence[TimedWord], picks: Sequence[PickTime]) -> str:
    """Insert `[n]` after the last word whose END is at or before the pick time.

    Word ends are used because Parakeet's first-word start can be ~0.4 s early (spike 6).
    A pick before any word ended is prefixed to the text.
    """
    slots: dict[int, list[int]] = {}
    for pick in sorted(picks, key=lambda p: (p.created_at, p.id)):
        pos = 0
        for i, w in enumerate(words):
            if w.end <= pick.created_at:
                pos = i + 1
        slots.setdefault(pos, []).append(pick.id)
    parts: list[str] = []
    if 0 in slots:
        parts.append(" ".join(f"[{i}]" for i in slots[0]))
    for i, w in enumerate(words):
        markers = slots.get(i + 1)
        parts.append(_attach(w.text, markers) if markers else w.text)
    return " ".join(p for p in parts if p)


def assemble_turn(
    segments: Sequence[Segment], picks: Iterable[PickTime], end: float
) -> AssembledTurn:
    """The marked text of a (possibly multi-segment) turn and the picks bound to it."""
    window = turn_window(segments, end)
    bound = bind_picks(picks, window)
    words = [w for seg in segments for w in seg.words]
    if words and all(seg.words or not seg.text.strip() for seg in segments):
        text = place_markers(words, bound)
    else:
        # No word timings for some segment: keep the text and append the markers.
        text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        tags = " ".join(f"[{p.id}]" for p in bound)
        text = f"{text} {tags}".strip()
    return AssembledTurn(text=text, pick_ids=tuple(p.id for p in bound), window=window)


# --- turn continuation ---------------------------------------------------------------


def spoken_words(audio_started_at: float | None, at: float) -> float:
    """Roughly how many words of the agent's reply had played by `at`."""
    if audio_started_at is None or at <= audio_started_at:
        return 0.0
    return (at - audio_started_at) / 1000.0 * SPOKEN_WORDS_PER_SEC


def can_continue(turn_end: float, resumed_at: float, audio_started_at: float | None) -> bool:
    """Rule 1: the user resumed soon after the turn ended and has heard little or none
    of the reply, so the new speech continues that turn."""
    gap = max(0.0, resumed_at - turn_end)
    heard = spoken_words(audio_started_at, resumed_at)
    return gap <= CONTINUATION_GAP_MS and heard <= CONTINUATION_MAX_SPOKEN_WORDS


def pick_hold_until(now: float, picks: Iterable[PickTime]) -> float | None:
    """Rule 2: if a pick happened within PICK_HOLD_MS before `now`, the time to wait
    until before submitting the turn; else None."""
    recent = [p.created_at for p in picks if p.created_at <= now + PICK_HOLD_MS]
    if not recent:
        return None
    until = max(recent) + PICK_HOLD_MS
    return until if until > now else None


def turn_hold_until(
    now: float, picks: Iterable[PickTime], debounce_ms: float = TURN_DEBOUNCE_MS
) -> float | None:
    """When to submit a turn that just ended: after the debounce window, or later if a
    pick asks for a longer hold. None: submit now."""
    until = max(pick_hold_until(now, picks) or now, now + debounce_ms)
    return until if until > now else None


@dataclass
class SubmittedTurn:
    """A turn handed to the talker; kept so a continuation can merge into it."""

    turn_id: str
    segments: list[Segment]
    end: float
    window: tuple[float, float]
    pick_ids: tuple[int, ...]
    text: str


@dataclass
class TurnTracker:
    """Segments of the turn being spoken, plus the last submitted turn.

    The call feeds it speech starts, STT segments and turn ends; it says whether a
    finished turn continues the previous one. Pure: times are passed in.
    """

    open: list[Segment] = field(default_factory=list[Segment])
    last: SubmittedTurn | None = None
    _resume_ok: bool = False
    _in_speech: bool = False

    def speech_started(self, now: float, audio_started_at: float | None) -> None:
        """VAD heard speech. Decides (once per speech start) whether it may continue the
        last turn: only while nothing new has been said since that turn ended."""
        if self._in_speech:
            return
        self._in_speech = True
        if self.last is not None and not self.open:
            self._resume_ok = can_continue(self.last.end, now, audio_started_at)

    def speech_stopped(self) -> None:
        self._in_speech = False

    def add_segment(self, segment: Segment) -> None:
        if segment.text.strip():
            self.open.append(segment)

    def take(self) -> tuple[list[Segment], SubmittedTurn | None]:
        """The finished turn's segments (merged with the last turn's when this is a
        continuation) and the turn it continues, if any. Clears the open turn."""
        merged_into = self.last if self._resume_ok else None
        segments = [*(merged_into.segments if merged_into else []), *self.open]
        self.open = []
        self._resume_ok = False
        return segments, merged_into

    def put_back(self, segments: list[Segment], *, continuation: bool = False) -> None:
        """A held turn the user resumed before it was submitted: it stays open (and
        still continues the last turn if it did)."""
        self.open = [*segments, *self.open]
        if continuation:
            self._resume_ok = True

    def submitted(self, turn: SubmittedTurn) -> None:
        self.last = turn

    def late_pick_fits(self, pick: PickTime, now: float, audio_started_at: float | None) -> bool:
        """A pick that arrived after its turn was submitted (the screenshot upload is
        slow) but was made during it, or within LATE_PICK_MS after it: re-submit if the
        user has heard little of the reply and hasn't started a new turn."""
        last = self.last
        if last is None or self.open or self._in_speech or pick.id in last.pick_ids:
            return False
        lo, hi = last.window
        if not lo <= pick.created_at <= hi + LATE_PICK_MS:
            return False
        return spoken_words(audio_started_at, now) <= CONTINUATION_MAX_SPOKEN_WORDS
