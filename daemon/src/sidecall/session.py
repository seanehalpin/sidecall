"""RepoSession: everything the daemon keeps per repo (SPEC §6.2-6.6, §9.2).

Owns the queue, approvals, transcript and session store, and drives the talker and
worker through small protocols so tests can inject fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from .approvals import ApprovalBroker
from .config import RepoDirs
from .paths import normalise_brief
from .prompts import UNDO_BRIEF, render_talker_turn, task_note
from .protocol.messages import (
    AgentTextDelta,
    AgentTextDeltaPayload,
    AgentTextDone,
    AgentTextDonePayload,
    Notify,
    NotifyPayload,
    QueueState,
    QueueStatePayload,
    ServerMessage,
    TaskCompleted,
    TaskCompletedPayload,
    TaskCreated,
    TaskEvent,
    TaskEventKind,
    TaskEventPayload,
    TaskFailed,
    TaskFailedPayload,
    TranscriptFinal,
    TranscriptFinalPayload,
)
from .protocol.models import (
    Brief,
    Preset,
    RepoInfo,
    RepoState,
    Role,
    Settings,
    Task,
    TranscriptEntry,
)
from .queue import TaskQueue
from .state import SessionStore, TranscriptStore

log = logging.getLogger(__name__)

Emit = Callable[[ServerMessage], None]
OnDelta = Callable[[str], None]
OnEvent = Callable[[TaskEventKind, str], None]


class Talker(Protocol):
    async def reply(self, prompt: str, on_delta: OnDelta) -> str:
        """Run one turn, streaming text through `on_delta`; return the full text."""
        ...

    async def interrupt(self) -> None: ...

    async def close(self) -> None: ...


@dataclass
class WorkerResult:
    status: Literal["completed", "failed", "cancelled"]
    summary: str = ""
    files: list[str] = field(default_factory=list[str])
    error: str | None = None


class Worker(Protocol):
    async def run(self, task: Task, on_event: OnEvent) -> WorkerResult: ...

    async def interrupt(self) -> None: ...

    async def close(self) -> None: ...


TalkerFactory = Callable[["RepoSession"], Talker]
WorkerFactory = Callable[["RepoSession"], Worker]


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_ms() -> float:
    return time.time() * 1000


class RepoSession:
    def __init__(
        self,
        repo_path: Path,
        home: Path,
        emit: Emit,
        talker_factory: TalkerFactory,
        worker_factory: WorkerFactory,
        talker_model: str | None = None,
        worker_model: str | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.dirs = RepoDirs.create(home, repo_path)
        self.talker_model = talker_model
        self.worker_model = worker_model
        self._emit = emit
        self._talker_factory = talker_factory
        self._worker_factory = worker_factory

        self.queue = TaskQueue(self.dirs.queue)
        self.queue.load()
        self.transcript = TranscriptStore(self.dirs.transcripts)
        self.sessions = SessionStore(self.dirs.sessions)
        self.approvals = ApprovalBroker(
            emit, on_blocked=self._on_blocked, on_unblocked=self._on_unblocked
        )

        self._talker: Talker | None = None
        self._worker: Worker | None = None
        self._talker_lock = asyncio.Lock()
        self._talker_busy = False
        self._unseen: list[str] = []
        self._cancel_requested: set[str] = set()
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._bg: set[asyncio.Task[None]] = set()

    # --- lifecycle ---

    def start(self) -> None:
        if self._runner is None:
            self._runner = asyncio.create_task(self._run_loop(), name=f"queue:{self.repo_path}")
        self._wake.set()

    async def close(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None
        for t in list(self._bg):
            t.cancel()
        for client in (self._talker, self._worker):
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.close()
        self._talker = self._worker = None

    # --- state ---

    @property
    def preset(self) -> Preset:
        return self.sessions.preset

    def set_preset(self, preset: Preset) -> None:
        self.sessions.set_preset(preset)

    def repo_info(self, origin: str) -> RepoInfo:
        return RepoInfo(name=self.repo_path.name, path=str(self.repo_path), origin=origin)

    def state(self) -> RepoState:
        return RepoState(
            tasks=self.queue.all_tasks(),
            approvals=self.approvals.pending(),
            transcript=self.transcript.recent(),
            settings=Settings(preset=self.preset),
        )

    def emit(self, msg: ServerMessage) -> None:
        self._emit(msg)

    def _emit_queue(self) -> None:
        self._emit(QueueState(payload=QueueStatePayload(tasks=self.queue.all_tasks())))

    def _add_transcript(
        self,
        role: Role,
        text: str,
        *,
        turn_id: str | None = None,
        pick_ids: list[int] | None = None,
        task_id: str | None = None,
    ) -> TranscriptEntry:
        entry = TranscriptEntry(
            turn_id=turn_id or _new_id(),
            role=role,
            text=text,
            pick_ids=pick_ids or [],
            ts=_now_ms(),
            task_id=task_id,
        )
        self.transcript.append(entry)
        self._emit(
            TranscriptFinal(
                payload=TranscriptFinalPayload(
                    turn_id=entry.turn_id,
                    role=role,
                    text=text,
                    pick_ids=entry.pick_ids,
                    task_id=task_id,
                )
            )
        )
        return entry

    # --- user turns (talker) ---

    def submit_user_text(self, text: str, pick_ids: list[int] | None = None) -> asyncio.Task[None]:
        """Record the user's text now and run the talker turn in the background."""
        self._add_transcript("user", text, pick_ids=pick_ids)
        task = asyncio.create_task(self._user_turn(text))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def _user_turn(self, text: str) -> None:
        # A new user turn interrupts the talker's current reply (SPEC §9.2).
        if self._talker_busy and self._talker is not None:
            with contextlib.suppress(Exception):
                await self._talker.interrupt()
        async with self._talker_lock:
            await self._run_talker_turn(text)

    async def _run_talker_turn(self, text: str) -> None:
        notes, self._unseen = self._unseen, []
        prompt = render_talker_turn(text, notes)
        turn_id = _new_id()
        parts: list[str] = []

        def on_delta(delta: str) -> None:
            parts.append(delta)
            self._emit(AgentTextDelta(payload=AgentTextDeltaPayload(turn_id=turn_id, delta=delta)))

        self._talker_busy = True
        full = ""
        try:
            if self._talker is None:
                self._talker = self._talker_factory(self)
            full = await self._talker.reply(prompt, on_delta)
        except Exception as exc:
            log.exception("talker turn failed")
            self._unseen = notes + self._unseen
            talker, self._talker = self._talker, None
            if talker is not None:
                with contextlib.suppress(Exception):
                    await talker.close()
            self._emit(AgentTextDone(payload=AgentTextDonePayload(turn_id=turn_id)))
            self._add_transcript("system", f"The talker hit an error: {exc}")
            return
        finally:
            self._talker_busy = False
        reply = (full or "".join(parts)).strip()
        self._emit(AgentTextDone(payload=AgentTextDonePayload(turn_id=turn_id)))
        if reply:
            self._add_transcript("agent", reply, turn_id=turn_id)

    # --- talker tools ---

    def dispatch(self, brief: Brief) -> Task:
        brief = normalise_brief(brief, self.repo_path)
        task = Task(id=_new_id(), title=brief.title, brief=brief, created_at=_now_ms())
        self.queue.add(task)
        self._emit(TaskCreated(payload=task))
        self._emit_queue()
        self._add_transcript("system", f"Task queued: {task.title}", task_id=task.id)
        self._wake.set()
        return task

    def request_undo(self) -> Task:
        return self.dispatch(UNDO_BRIEF.model_copy(deep=True))

    def get_task(self, task_id: str) -> Task | None:
        return self.queue.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        task, was_running = self.queue.cancel(task_id)
        if task is None:
            return False
        if not was_running:
            self._emit_queue()
            self._add_transcript("system", f"Task cancelled: {task.title}", task_id=task.id)
            self._unseen.append(task_note(task))
            return True
        self._cancel_requested.add(task_id)
        self.approvals.cancel_task(task_id)
        if self._worker is not None:
            with contextlib.suppress(Exception):
                await self._worker.interrupt()
        return True

    def reorder(self, ids: list[str]) -> None:
        self.queue.reorder(ids)
        self._emit_queue()

    def respond_approval(self, approval_id: str, allow: bool) -> bool:
        return self.approvals.respond(approval_id, allow)

    # --- approvals -> task status ---

    def _on_blocked(self, task_id: str) -> None:
        if self.queue.mark_blocked(task_id) is not None:
            self._emit_queue()

    def _on_unblocked(self, task_id: str) -> None:
        if self.queue.mark_running(task_id) is not None:
            self._emit_queue()

    # --- queue runner (worker) ---

    async def _run_loop(self) -> None:
        while True:
            task = self.queue.next_runnable()
            if task is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            try:
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("queue runner error")

    async def _run_task(self, task: Task) -> None:
        if self.queue.mark_running(task.id) is None:
            return
        self._emit_queue()

        def on_event(kind: TaskEventKind, text: str) -> None:
            self.queue.set_last_event(task.id, text)
            self._emit(TaskEvent(payload=TaskEventPayload(task_id=task.id, kind=kind, text=text)))

        on_event("status", "Started")
        try:
            if self._worker is None:
                self._worker = self._worker_factory(self)
            result = await self._worker.run(task, on_event)
        except Exception as exc:
            log.exception("worker crashed")
            worker, self._worker = self._worker, None  # recreate on the next task
            if worker is not None:
                with contextlib.suppress(Exception):
                    await worker.close()
            result = WorkerResult("failed", error=f"Worker error: {exc}")

        if task.id in self._cancel_requested:
            self._cancel_requested.discard(task.id)
            result = WorkerResult("cancelled")
        self._finish(task, result)

    def _finish(self, task: Task, result: WorkerResult) -> None:
        if result.status == "completed":
            done = self.queue.mark_completed(task.id, result.summary, result.files)
            if done is None:
                return
            self._emit(
                TaskCompleted(
                    payload=TaskCompletedPayload(
                        task_id=task.id, summary=result.summary, files=result.files
                    )
                )
            )
            self._emit_queue()
            text = f"Task completed: {task.title}. {result.summary}".strip()
            self._add_transcript("system", text, task_id=task.id)
            self._emit(
                Notify(
                    payload=NotifyPayload(
                        title=f"{task.title} done", body=result.summary or "Done", action="open"
                    )
                )
            )
        elif result.status == "failed":
            reason = result.error or "unknown error"
            done = self.queue.mark_failed(task.id, reason)
            if done is None:
                return
            self._emit(TaskFailed(payload=TaskFailedPayload(task_id=task.id, reason=reason)))
            self._emit_queue()
            self._add_transcript("system", f"Task failed: {task.title}. {reason}", task_id=task.id)
            self._emit(
                Notify(
                    payload=NotifyPayload(title=f"{task.title} failed", body=reason, action="open")
                )
            )
        else:
            done = self.queue.mark_cancelled(task.id)
            if done is None:
                return
            self._emit_queue()
            self._add_transcript("system", f"Task cancelled: {task.title}", task_id=task.id)
        self._unseen.append(task_note(done))
