from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from sidecall.config import (
    DEFAULT_EXTENSION_ID,
    Config,
    RepoDirs,
    atomic_write_text,
    repo_key,
    sidecall_home,
)
from sidecall.protocol.models import TranscriptEntry
from sidecall.state import TRANSCRIPT_MEMORY, SessionStore, TranscriptStore


def test_sidecall_home_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SIDECALL_HOME", str(tmp_path / "h"))
    assert sidecall_home() == tmp_path / "h"
    monkeypatch.delenv("SIDECALL_HOME")
    assert sidecall_home() == Path.home() / ".sidecall"


def test_config_first_run_creates_token(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    assert len(cfg.token) > 30
    assert cfg.port == 7823
    assert cfg.extension_id == DEFAULT_EXTENSION_ID
    assert cfg.talker_model == "sonnet"
    assert cfg.call_talker_model == "haiku"
    assert cfg.worker_model is None
    data = tomllib.loads((tmp_path / "config.toml").read_text())
    assert data["token"] == cfg.token
    assert "worker_model" not in data
    assert "talker_model" not in data  # defaults aren't written, so they can change
    assert (tmp_path / "config.toml").stat().st_mode & 0o077 == 0
    # stable across loads
    assert Config.load(tmp_path).token == cfg.token


def test_config_round_trip_with_repos(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    cfg.worker_model = "sonnet"
    cfg.port = 9000
    cfg.map_repo("http://localhost:5173", tmp_path / "app")
    again = Config.load(tmp_path)
    assert again.repos == {"http://localhost:5173": str(tmp_path / "app")}
    assert again.worker_model == "sonnet"
    assert again.port == 9000


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "d" / "f.json"
    atomic_write_text(target, "one")
    atomic_write_text(target, "two")
    assert target.read_text() == "two"
    assert [p.name for p in target.parent.iterdir()] == ["f.json"]


def test_repo_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    dirs = RepoDirs.create(tmp_path / "home", repo)
    assert dirs.root.name == repo_key(repo) and len(dirs.root.name) == 16
    assert dirs.notes.read_text() == ""
    assert dirs.transcripts.is_dir()
    link = tmp_path / "link"
    link.symlink_to(repo)
    assert repo_key(link) == repo_key(repo)


def _entry(i: int, ts: float) -> TranscriptEntry:
    return TranscriptEntry(turn_id=f"u{i}", role="user", text=f"msg {i}", ts=ts)


def test_transcript_appends_by_day_and_reloads_last_50(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "t")
    day1 = 1_700_000_000_000.0
    day2 = day1 + 86_400_000
    for i in range(40):
        store.append(_entry(i, day1 + i))
    for i in range(40, 70):
        store.append(_entry(i, day2 + i))
    files = sorted(p.name for p in (tmp_path / "t").glob("*.jsonl"))
    assert len(files) == 2
    assert len(store.recent()) == TRANSCRIPT_MEMORY
    reloaded = TranscriptStore(tmp_path / "t")
    texts = [e.text for e in reloaded.recent()]
    assert len(texts) == TRANSCRIPT_MEMORY
    assert texts[0] == "msg 20" and texts[-1] == "msg 69"


def test_session_store(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    s = SessionStore(path)
    assert s.preset == "default" and s.data.talker_session_id is None
    s.set_talker_session("abc")
    s.set_worker_session("def")
    s.set_preset("cautious")
    s2 = SessionStore(path)
    assert s2.data.talker_session_id == "abc"
    assert s2.data.worker_session_id == "def"
    assert s2.preset == "cautious"


def test_session_store_ignores_garbage(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text("{not json")
    assert SessionStore(path).preset == "default"


def test_transcript_replace_updates_entry_and_survives_reload(tmp_path: Path) -> None:
    store = TranscriptStore(tmp_path / "t")
    first = TranscriptEntry(turn_id="u1", role="user", text="make this", ts=1000.0)
    store.append(first)
    store.append(TranscriptEntry(turn_id="a1", role="agent", text="Sure.", ts=1001.0))
    store.replace(first.model_copy(update={"text": "make this bigger", "ts": 1002.0}))
    assert [(e.turn_id, e.text, e.ts) for e in store.recent()] == [
        ("u1", "make this bigger", 1000.0),
        ("a1", "Sure.", 1001.0),
    ]
    reloaded = TranscriptStore(tmp_path / "t")
    assert [(e.turn_id, e.text, e.ts) for e in reloaded.recent()] == [
        ("u1", "make this bigger", 1000.0),
        ("a1", "Sure.", 1001.0),
    ]
    # Replacing an unknown entry appends it.
    store.replace(TranscriptEntry(turn_id="u2", role="user", text="hi", ts=1003.0))
    assert store.recent()[-1].text == "hi"
