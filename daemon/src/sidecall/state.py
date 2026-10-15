"""Per-repo persistent state: transcripts (jsonl by date) and sessions.json (SPEC §6.2)."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .config import atomic_write_text
from .handoff import WorkerSessionStats
from .protocol.models import Model, Preset, TranscriptEntry, WorkerModel

log = logging.getLogger(__name__)

TRANSCRIPT_MEMORY = 50


class TranscriptStore:
    """Append-only transcript: `transcripts/<YYYY-MM-DD>.jsonl`, last 50 kept in memory."""

    def __init__(self, directory: Path, cleared_at: float = 0.0) -> None:
        self.directory = directory
        self.cleared_at = cleared_at
        """Entries before this (epoch ms) were cleared from the chat; the files keep them."""
        self._recent: deque[TranscriptEntry] = deque(maxlen=TRANSCRIPT_MEMORY)
        self._load_recent()

    def clear(self, now: float) -> None:
        self.cleared_at = now
        self._recent.clear()

    def recent(self) -> list[TranscriptEntry]:
        return list(self._recent)

    def append(self, entry: TranscriptEntry) -> None:
        self._recent.append(entry)
        self._write(entry)

    def replace(self, entry: TranscriptEntry) -> None:
        """Update the entry with the same turnId and role (a continued voice turn). The
        day file gets the new version too; loading keeps the last one."""
        for i, old in enumerate(self._recent):
            if old.turn_id == entry.turn_id and old.role == entry.role:
                self._recent[i] = entry.model_copy(update={"ts": old.ts})
                self._write(entry)
                return
        self.append(entry)

    def _write(self, entry: TranscriptEntry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        day = datetime.fromtimestamp(entry.ts / 1000).strftime("%Y-%m-%d")
        with (self.directory / f"{day}.jsonl").open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def _load_recent(self) -> None:
        if not self.directory.exists():
            return
        entries: list[TranscriptEntry] = []
        # File names sort chronologically; read newest files until we have enough.
        for path in sorted(self.directory.glob("*.jsonl"), reverse=True):
            lines = path.read_text(encoding="utf-8").splitlines()
            batch: list[TranscriptEntry] = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    batch.append(TranscriptEntry.model_validate_json(line))
                except ValidationError:
                    log.warning("skipping bad transcript line in %s", path)
            entries = batch + entries
            if len(entries) >= TRANSCRIPT_MEMORY:
                break
        kept = [e for e in _dedupe(entries) if e.ts >= self.cleared_at]
        self._recent.extend(kept[-TRANSCRIPT_MEMORY:])


def _dedupe(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Replaced entries (same turnId and role) keep their first position, last text."""
    index: dict[tuple[str, str], int] = {}
    out: list[TranscriptEntry] = []
    for entry in entries:
        key = (entry.turn_id, entry.role)
        if key in index:
            out[index[key]] = entry.model_copy(update={"ts": out[index[key]].ts})
        else:
            index[key] = len(out)
            out.append(entry)
    return out


class SessionData(Model):
    talker_session_id: str | None = None
    worker_session_id: str | None = None
    preset: Preset = "default"
    clear_chat_daily: bool = True
    worker_model: WorkerModel | None = None
    """Chosen in the UI; None falls back to config.toml's worker_model, then Sonnet."""
    chat_cleared_at: float = 0.0
    """Epoch ms of the last chat clear (manual or daily)."""
    # About the worker session above, for rotation (handoff.py).
    worker_last_result_at: float | None = None
    worker_context_tokens: int | None = None
    worker_session_cost: float = 0.0
    # The SDK's last total_cost_usd for each session above: a resumed session's total
    # includes its history, so per-turn cost is measured from here (costs.py).
    talker_sdk_total: float | None = None
    worker_sdk_total: float | None = None


class SessionStore:
    """`sessions.json`: talker/worker session ids for resume, plus the repo's preset."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = SessionData()
        if path.exists():
            try:
                self.data = SessionData.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, json.JSONDecodeError):
                log.warning("ignoring unreadable %s", path)

    @property
    def preset(self) -> Preset:
        return self.data.preset

    def set_preset(self, preset: Preset) -> None:
        self.data.preset = preset
        self._save()

    def set_worker_model(self, model: WorkerModel) -> None:
        self.data.worker_model = model
        self._save()

    def set_clear_chat_daily(self, on: bool) -> None:
        self.data.clear_chat_daily = on
        self._save()

    def set_chat_cleared(self, now: float) -> None:
        self.data.chat_cleared_at = now
        self.data.talker_session_id = None  # the typed talker starts over too
        self.data.talker_sdk_total = None
        self._save()

    def set_talker_session(self, session_id: str | None, sdk_total: float | None = None) -> None:
        if session_id != self.data.talker_session_id or sdk_total != self.data.talker_sdk_total:
            self.data.talker_session_id = session_id
            self.data.talker_sdk_total = sdk_total if session_id else None
            self._save()

    def set_worker_session(self, session_id: str | None) -> None:
        if session_id != self.data.worker_session_id:
            self.data.worker_session_id = session_id
            self._save()

    def worker_stats(self) -> WorkerSessionStats:
        d = self.data
        return WorkerSessionStats(
            d.worker_last_result_at, d.worker_context_tokens, d.worker_session_cost
        )

    def record_worker_result(
        self,
        session_id: str,
        turn_cost: float | None,
        context_tokens: int | None,
        now: float,
        sdk_total: float | None = None,
    ) -> None:
        d = self.data
        if session_id != d.worker_session_id:
            d.worker_session_id = session_id
            d.worker_session_cost = 0.0
        d.worker_sdk_total = sdk_total
        d.worker_last_result_at = now
        if context_tokens is not None:
            d.worker_context_tokens = context_tokens
        d.worker_session_cost += turn_cost or 0.0
        self._save()

    def reset_worker(self) -> None:
        """Forget the worker session (rotation): the next task starts a new one."""
        d = self.data
        d.worker_session_id = None
        d.worker_last_result_at = d.worker_context_tokens = d.worker_sdk_total = None
        d.worker_session_cost = 0.0
        self._save()

    def _save(self) -> None:
        atomic_write_text(self.path, self.data.model_dump_json(indent=2) + "\n")
