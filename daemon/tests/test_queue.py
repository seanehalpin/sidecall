from __future__ import annotations

import json
from pathlib import Path

from helpers import make_task

from sidecall.protocol.models import Task
from sidecall.queue import MAX_RECENT, TaskQueue


def ids(tasks: list[Task]) -> list[str]:
    return [t.id for t in tasks]


def next_id(q: TaskQueue) -> str | None:
    task = q.next_runnable()
    return task.id if task else None


def test_fifo_and_one_running_at_a_time() -> None:
    q = TaskQueue()
    a, b = q.add(make_task("a", "a")), q.add(make_task("b", "b"))
    assert q.next_runnable() is a
    q.mark_running("a")
    assert q.next_runnable() is None
    assert q.mark_running("b") is None  # can't start a second task
    assert q.running() is a
    q.mark_completed("a", "done", ["x.css"])
    assert q.next_runnable() is b
    assert a.status == "completed" and a.summary == "done" and a.files == ["x.css"]
    assert a.finished_at is not None and a.started_at is not None


def test_blocked_and_back_to_running() -> None:
    q = TaskQueue()
    q.add(make_task("a", "a"))
    assert q.mark_blocked("a") is None  # only a running task can block
    q.mark_running("a")
    blocked = q.mark_blocked("a")
    assert blocked is not None and blocked.status == "blocked"
    assert q.running() is blocked and q.next_runnable() is None
    resumed = q.mark_running("a")
    assert resumed is not None and resumed.status == "running"


def test_failed_and_cancelled() -> None:
    q = TaskQueue()
    q.add(make_task("a", "a"))
    q.mark_running("a")
    failed = q.mark_failed("a", "boom")
    assert failed is not None and failed.status == "failed" and failed.error == "boom"
    assert q.mark_failed("a", "again") is None


def test_cancel_queued_vs_running() -> None:
    q = TaskQueue()
    q.add(make_task("a", "a"))
    q.add(make_task("b", "b"))
    q.mark_running("a")
    task, was_running = q.cancel("b")
    assert task is not None and not was_running and task.status == "cancelled"
    task, was_running = q.cancel("a")
    assert task is not None and was_running and task.status == "running"
    q.mark_cancelled("a")
    assert ids(q.active) == [] and ids(q.recent) == ["b", "a"]
    assert q.cancel("nope") == (None, False)


def test_reorder_only_touches_queued() -> None:
    q = TaskQueue()
    for name in "abcde":
        q.add(make_task(name, name))
    q.mark_running("a")
    q.reorder(["d", "zzz", "a", "b", "d"])
    # running stays first; listed queued ids next; unlisted keep their order
    assert ids(q.active) == ["a", "d", "b", "c", "e"]
    assert q.next_runnable() is None
    q.mark_completed("a", "", [])
    assert next_id(q) == "d"


def test_recent_is_capped() -> None:
    q = TaskQueue()
    for i in range(MAX_RECENT + 5):
        t = q.add(make_task(f"t{i}", f"t{i}"))
        q.cancel(t.id)
    assert len(q.recent) == MAX_RECENT
    assert q.recent[-1].id == f"t{MAX_RECENT + 4}"
    assert q.get("t0") is None


def test_persistence_and_restart_requeues_running_at_front(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    q = TaskQueue(path)
    q.add(make_task("done", "done"))
    q.mark_running("done")
    q.mark_completed("done", "ok", [])
    q.add(make_task("a", "a"))
    q.add(make_task("b", "b"))
    q.add(make_task("c", "c"))
    q.reorder(["c"])
    q.mark_running("c")
    data = json.loads(path.read_text())
    assert [t["id"] for t in data["active"]] == ["c", "a", "b"]
    assert data["active"][0]["status"] == "running"
    assert "createdAt" in data["active"][0]  # camelCase, same as the wire

    q2 = TaskQueue(path)
    q2.load()
    assert ids(q2.active) == ["c", "a", "b"]
    assert all(t.status == "queued" for t in q2.active)
    assert ids(q2.recent) == ["done"]
    assert next_id(q2) == "c"
    # load() persists the requeue
    assert json.loads(path.read_text())["active"][0]["status"] == "queued"


def test_blocked_task_also_requeued(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    q = TaskQueue(path)
    q.add(make_task("a", "a"))
    q.add(make_task("b", "b"))
    q.reorder(["b"])
    q.mark_running("b")
    q.mark_blocked("b")
    q2 = TaskQueue(path)
    q2.load()
    assert ids(q2.active) == ["b", "a"]
    assert q2.active[0].status == "queued"


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "nope.json")
    q.load()
    assert q.all_tasks() == []
