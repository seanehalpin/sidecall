"""Per-repo task queue (SPEC §6.6): FIFO, one running task, persisted to queue.json."""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import TypeAdapter

from .config import atomic_write_text
from .protocol.models import Task, TaskStatus

ACTIVE: frozenset[TaskStatus] = frozenset({"queued", "running", "blocked"})
FINISHED: frozenset[TaskStatus] = frozenset({"completed", "failed", "cancelled"})
MAX_RECENT = 20

_tasks_adapter: TypeAdapter[list[Task]] = TypeAdapter(list[Task])


class TaskQueue:
    """Active tasks in run order, plus the most recent finished tasks.

    Every mutation persists to `path` (when set). Mutators return the updated task,
    or None when the id is unknown or the transition doesn't apply.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._active: list[Task] = []
        self._recent: list[Task] = []

    # --- reading ---

    @property
    def active(self) -> list[Task]:
        return list(self._active)

    @property
    def recent(self) -> list[Task]:
        return list(self._recent)

    def all_tasks(self) -> list[Task]:
        """Active tasks in run order, then finished tasks (oldest first)."""
        return [*self._active, *self._recent]

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.all_tasks() if t.id == task_id), None)

    def running(self) -> Task | None:
        return next((t for t in self._active if t.status in ("running", "blocked")), None)

    def next_runnable(self) -> Task | None:
        """The next queued task, unless one is already running."""
        if self.running() is not None:
            return None
        return next((t for t in self._active if t.status == "queued"), None)

    # --- mutating ---

    def add(self, task: Task) -> Task:
        task.status = "queued"
        self._active.append(task)
        self._save()
        return task

    def mark_running(self, task_id: str) -> Task | None:
        task = self._find_active(task_id)
        if task is None:
            return None
        if task.status == "queued" and self.running() not in (None, task):
            return None
        if task.started_at is None:
            task.started_at = _now_ms()
        task.status = "running"
        self._save()
        return task

    def mark_blocked(self, task_id: str) -> Task | None:
        task = self._find_active(task_id)
        if task is None or task.status != "running":
            return None
        task.status = "blocked"
        self._save()
        return task

    def set_last_event(self, task_id: str, text: str) -> Task | None:
        task = self._find_active(task_id)
        if task is None:
            return None
        task.last_event = text
        return task  # not persisted: progress lines are transient

    def mark_completed(self, task_id: str, summary: str, files: list[str]) -> Task | None:
        return self._finish(task_id, "completed", summary=summary, files=files)

    def mark_failed(self, task_id: str, error: str) -> Task | None:
        return self._finish(task_id, "failed", error=error)

    def mark_cancelled(self, task_id: str) -> Task | None:
        return self._finish(task_id, "cancelled")

    def cancel(self, task_id: str) -> tuple[Task | None, bool]:
        """Cancel a task. Returns (task, was_running).

        A queued task is finished as cancelled right away. A running or blocked task
        is left as is (the caller interrupts the worker, then calls mark_cancelled).
        """
        task = self._find_active(task_id)
        if task is None:
            return None, False
        if task.status == "queued":
            self._finish(task_id, "cancelled")
            return task, False
        return task, True

    def reorder(self, ids: list[str]) -> list[Task]:
        """Reorder queued tasks: listed ids first (in the given order), then the other
        queued tasks in their current order. Unknown or non-queued ids are ignored.
        Running/blocked tasks keep their place at the front."""
        queued = [t for t in self._active if t.status == "queued"]
        by_id = {t.id: t for t in queued}
        listed = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
        listed_ids = {t.id for t in listed}
        rest = [t for t in queued if t.id not in listed_ids]
        in_progress = [t for t in self._active if t.status != "queued"]
        self._active = [*in_progress, *listed, *rest]
        self._save()
        return self.active

    # --- persistence ---

    def load(self) -> None:
        """Load from disk. A task left running or blocked goes back to the front, queued."""
        if self.path is None or not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        active = _tasks_adapter.validate_python(data.get("active", []))
        recent = _tasks_adapter.validate_python(data.get("recent", []))
        interrupted = [t for t in active if t.status in ("running", "blocked")]
        waiting = [t for t in active if t.status == "queued"]
        for t in interrupted:
            t.status = "queued"
        self._active = [*interrupted, *waiting]
        self._recent = recent[-MAX_RECENT:]
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        payload = {
            "active": _tasks_adapter.dump_python(self._active, mode="json", by_alias=True),
            "recent": _tasks_adapter.dump_python(self._recent, mode="json", by_alias=True),
        }
        atomic_write_text(self.path, json.dumps(payload, indent=2) + "\n")

    # --- internals ---

    def _find_active(self, task_id: str) -> Task | None:
        return next((t for t in self._active if t.id == task_id), None)

    def _finish(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        summary: str | None = None,
        files: list[str] | None = None,
        error: str | None = None,
    ) -> Task | None:
        task = self._find_active(task_id)
        if task is None:
            return None
        self._active.remove(task)
        task.status = status
        task.finished_at = _now_ms()
        if summary is not None:
            task.summary = summary
        if files is not None:
            task.files = files
        if error is not None:
            task.error = error
        self._recent.append(task)
        self._recent = self._recent[-MAX_RECENT:]
        self._save()
        return task


def _now_ms() -> float:
    return time.time() * 1000
