from __future__ import annotations

from pathlib import Path

from sidecall.config import Config
from sidecall.handoff import (
    RotationPolicy,
    TaskLog,
    TaskLogEntry,
    WorkerSessionStats,
    render_seed,
    rotation_reason,
)
from sidecall.state import SessionStore

POLICY = RotationPolicy(idle_secs=900, max_context_tokens=120_000, max_session_cost=15.0)


def test_rotation_reasons() -> None:
    now = 10_000.0
    assert rotation_reason(POLICY, WorkerSessionStats(), now) is None
    assert rotation_reason(POLICY, WorkerSessionStats(now - 60, 50_000, 2.0), now) is None
    assert rotation_reason(POLICY, WorkerSessionStats(now - 1200), now) == "idle 20 min"
    assert rotation_reason(POLICY, WorkerSessionStats(now, 130_000), now) == "context 130000 tokens"
    assert rotation_reason(POLICY, WorkerSessionStats(now, 1, 15.5), now) == "session cost $15.50"


def entry(title: str, status: str = "completed") -> TaskLogEntry:
    return TaskLogEntry(1.0, title, f"do {title}", status, f"{title} done", ["src/a.svelte"])


def test_task_log_round_trip_keeps_the_latest(tmp_path: Path) -> None:
    log = TaskLog(tmp_path / "task-log.jsonl")
    assert log.recent() == []
    for i in range(25):
        log.append(entry(f"t{i}"))
    (tmp_path / "task-log.jsonl").open("a").write("not json\n")
    recent = log.recent(20)
    assert [e.title for e in recent] == [f"t{i}" for i in range(6, 25)]


def test_render_seed() -> None:
    seed = render_seed([entry("Red heading"), entry("Logo", "failed")], " M src/a.svelte\n", "")
    assert '- [completed] Red heading: asked "do Red heading". Outcome: Red heading done.' in seed
    assert "- [failed] Logo:" in seed
    assert "git status --short:\n M src/a.svelte" in seed
    assert "git log --oneline -10:\n(no commits)" in seed
    assert seed.endswith("---\n\n")


def test_session_store_tracks_worker_stats(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    store.record_worker_result("s1", 0.5, 40_000, 100.0)
    store.record_worker_result("s1", 0.25, 60_000, 200.0)
    stats = SessionStore(tmp_path / "sessions.json").worker_stats()  # persisted
    assert (stats.last_result_at, stats.context_tokens, stats.session_cost) == (200.0, 60_000, 0.75)
    store.record_worker_result("s2", 0.1, None, 300.0)  # a new session starts its own total
    assert store.worker_stats().session_cost == 0.1
    store.reset_worker()
    assert store.data.worker_session_id is None
    assert store.worker_stats() == WorkerSessionStats()


def test_rotation_config_round_trip(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path)
    assert cfg.rotation == POLICY
    assert "worker_idle_minutes" not in cfg.to_toml()
    cfg.worker_idle_minutes = 5
    cfg.save()
    assert Config.load(tmp_path).rotation.idle_secs == 300


def test_sdk_totals_are_kept_per_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    store.record_worker_result("s1", 0.5, 1000, 1.0, sdk_total=40.0)
    store.set_talker_session("t1", 2.5)
    again = SessionStore(tmp_path / "sessions.json").data
    assert (again.worker_sdk_total, again.talker_sdk_total) == (40.0, 2.5)
    store.reset_worker()
    store.set_chat_cleared(5.0)
    assert store.data.worker_sdk_total is None and store.data.talker_sdk_total is None
