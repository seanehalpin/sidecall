"""Per-repo persistent state: transcripts (jsonl by date) and sessions.json (SPEC §6.2)."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .config import atomic_write_text
from .protocol.models import Model, Preset, TranscriptEntry

log = logging.getLogger(__name__)

TRANSCRIPT_MEMORY = 50


class TranscriptStore:
    """Append-only transcript: `transcripts/<YYYY-MM-DD>.jsonl`, last 50 kept in memory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._recent: deque[TranscriptEntry] = deque(maxlen=TRANSCRIPT_MEMORY)
        self._load_recent()

    def recent(self) -> list[TranscriptEntry]:
        return list(self._recent)

    def append(self, entry: TranscriptEntry) -> None:
        self._recent.append(entry)
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
        self._recent.extend(entries[-TRANSCRIPT_MEMORY:])


class SessionData(Model):
    talker_session_id: str | None = None
    worker_session_id: str | None = None
    preset: Preset = "default"


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

    def set_talker_session(self, session_id: str | None) -> None:
        if session_id != self.data.talker_session_id:
            self.data.talker_session_id = session_id
            self._save()

    def set_worker_session(self, session_id: str | None) -> None:
        if session_id != self.data.worker_session_id:
            self.data.worker_session_id = session_id
            self._save()

    def _save(self) -> None:
        atomic_write_text(self.path, self.data.model_dump_json(indent=2) + "\n")
