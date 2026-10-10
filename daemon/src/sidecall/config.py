"""Global config (`~/.sidecall/config.toml`) and per-repo state directories (SPEC §6.2, §8)."""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w

DEFAULT_PORT = 7823
# ID of the key pinned in extension/wxt.config.ts (SPEC §6.1, §9.1).
DEFAULT_EXTENSION_ID = "kcjkimilmanlonlplfancfpoekkphjhb"
DEFAULT_TALKER_MODEL = "haiku"


def sidecall_home() -> Path:
    """`$SIDECALL_HOME`, or `~/.sidecall`."""
    env = os.environ.get("SIDECALL_HOME")
    return Path(env).expanduser() if env else Path.home() / ".sidecall"


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then `os.replace`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class Config:
    port: int = DEFAULT_PORT
    token: str = ""
    extension_id: str = DEFAULT_EXTENSION_ID
    talker_model: str | None = DEFAULT_TALKER_MODEL
    worker_model: str | None = None
    repos: dict[str, str] = field(default_factory=dict[str, str])
    path: Path = field(default_factory=lambda: sidecall_home() / "config.toml")

    @property
    def home(self) -> Path:
        return self.path.parent

    @classmethod
    def load(cls, home: Path | None = None) -> Config:
        """Load config, creating it (with a fresh token) on first run."""
        path = (home or sidecall_home()) / "config.toml"
        data: dict[str, Any] = {}
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        raw_repos: dict[str, Any] = data.get("repos", {})
        cfg = cls(
            port=int(data.get("port", DEFAULT_PORT)),
            token=str(data.get("token", "")),
            extension_id=str(data.get("extension_id", DEFAULT_EXTENSION_ID)),
            talker_model=_opt_str(data.get("talker_model", DEFAULT_TALKER_MODEL)),
            worker_model=_opt_str(data.get("worker_model")),
            repos={str(k): str(v) for k, v in raw_repos.items()},
            path=path,
        )
        if not cfg.token or not path.exists():
            cfg.token = cfg.token or secrets.token_urlsafe(32)
            cfg.save()
        return cfg

    def to_toml(self) -> str:
        data: dict[str, Any] = {
            "port": self.port,
            "token": self.token,
            "extension_id": self.extension_id,
        }
        # TOML has no null: an unset model is simply omitted (= CLI default).
        if self.talker_model:
            data["talker_model"] = self.talker_model
        if self.worker_model:
            data["worker_model"] = self.worker_model
        data["repos"] = dict(self.repos)
        return tomli_w.dumps(data)

    def save(self) -> None:
        atomic_write_text(self.path, self.to_toml())
        self.path.chmod(0o600)

    def map_repo(self, origin: str, repo_path: Path) -> None:
        self.repos[origin] = str(repo_path)
        self.save()


def _opt_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def repo_key(repo_path: Path) -> str:
    """Stable per-repo directory name: sha256 of the real path, first 16 hex chars."""
    real = os.path.realpath(repo_path)
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RepoDirs:
    """Paths of one repo's state directory, `<home>/repos/<hash>/`."""

    root: Path

    @classmethod
    def create(cls, home: Path, repo_path: Path) -> RepoDirs:
        dirs = cls(home / "repos" / repo_key(repo_path))
        dirs.transcripts.mkdir(parents=True, exist_ok=True)
        if not dirs.notes.exists():
            dirs.notes.write_text("", encoding="utf-8")
        return dirs

    @property
    def queue(self) -> Path:
        return self.root / "queue.json"

    @property
    def sessions(self) -> Path:
        return self.root / "sessions.json"

    @property
    def tasks(self) -> Path:
        """Per-task state: `tasks/<task_id>/picks.json` (SPEC §6.5)."""
        return self.root / "tasks"

    @property
    def notes(self) -> Path:
        return self.root / "notes.md"

    @property
    def transcripts(self) -> Path:
        return self.root / "transcripts"
