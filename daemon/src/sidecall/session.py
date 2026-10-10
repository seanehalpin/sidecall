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
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from .announcer import Announcement, Announcer, render_announcement
from .approvals import ApprovalBroker, NotifyAction
from .config import RepoDirs
from .paths import normalise_brief
from .picks import PickStore, load_snapshot, normalise_pick, save_snapshot
from .prompts import (
    UNDO_BRIEF,
    PickRef,
    interrupted_note,
    render_talker_turn,
    task_note,
)
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
    PageContext,
    Pick,
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
from .uploads import Image, LabelledImage, UploadStore
from .voice.turns import PickTime

if TYPE_CHECKING:
    from .voice.call import Call

log = logging.getLogger(__name__)

Emit = Callable[[ServerMessage], None]
OnDelta = Callable[[str], None]
OnEvent = Callable[[TaskEventKind, str], None]


class Talker(Protocol):
    async def reply(
        self,
        prompt: str,
        on_delta: OnDelta,
        image: Image | None = None,
        extra_images: Sequence[LabelledImage] = (),
        *,
        spoken: bool = False,
    ) -> str:
        """Run one turn (with an optional page screenshot, then labelled pick crops),
        streaming text through `on_delta`; return the full text. `spoken` turns stream
        text as it arrives (it is read aloud) instead of a message at a time."""
        ...

    async def interrupt(self) -> None: ...

    async def warm(self) -> None:
        """Get ready for a turn (connect), so the first spoken reply isn't slower."""
        ...

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


MAX_TURN_IMAGES = 6
"""Images per talker turn, the page screenshot included."""
MAX_WORKER_PICKS_WITH_VIEWPORT = 6
MAX_WORKER_IMAGES = 20


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
        self.uploads = UploadStore(home)
        self.picks = PickStore()
        self.approvals = ApprovalBroker(
            emit,
            on_blocked=self._on_blocked,
            on_unblocked=self._on_unblocked,
            notify_action=self._notify_action,
        )
        self.announcer = Announcer()
        self._call: Call | None = None
        self._last_page: PageContext | None = None
        # The latest page the extension reported during the current call.
        self._call_page: PageContext | None = None

        self._talker: Talker | None = None
        self._worker: Worker | None = None
        self._talker_lock = asyncio.Lock()
        self._talker_busy = False
        # The page of the talker turn in progress; tasks it dispatches inherit it.
        self._turn_page: PageContext | None = None
        # The picks of that turn, as the talker saw them (immune to later pick.remove).
        self._turn_picks: list[Pick] = []
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

    def new_turn_id(self) -> str:
        return _new_id()

    def add_transcript(
        self,
        role: Role,
        text: str,
        *,
        turn_id: str | None = None,
        pick_ids: list[int] | None = None,
        replace: bool = False,
    ) -> str:
        """Append (or update) a transcript entry (voice turns, SPEC §6.8) and broadcast it."""
        return self._add_transcript(
            role, text, turn_id=turn_id, pick_ids=pick_ids, replace=replace
        ).turn_id

    def _add_transcript(
        self,
        role: Role,
        text: str,
        *,
        turn_id: str | None = None,
        pick_ids: list[int] | None = None,
        task_id: str | None = None,
        replace: bool = False,
    ) -> TranscriptEntry:
        entry = TranscriptEntry(
            turn_id=turn_id or _new_id(),
            role=role,
            text=text,
            pick_ids=pick_ids or [],
            ts=_now_ms(),
            task_id=task_id,
        )
        if replace:
            self.transcript.replace(entry)
        else:
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

    # --- picks ---

    def add_pick(self, pick: Pick) -> Pick:
        """Normalise (SPEC §5.4) and store a pick sent by the extension."""
        normalised = normalise_pick(pick, self.repo_path)
        self.picks.add(normalised)
        if self._call is not None:
            self._call.pick_added(PickTime(normalised.id, normalised.created_at))
        return normalised

    def pick_times(self) -> list[PickTime]:
        return [PickTime(p.id, p.created_at) for p in self.picks.all()]

    def remove_pick(self, pick_id: int) -> bool:
        return self.picks.remove(pick_id)

    def clear_picks(self) -> None:
        self.picks.clear()

    def task_picks(self, task: Task) -> list[Pick]:
        """The picks a task was dispatched with, from its snapshot (survives restarts)."""
        if not task.pick_ids:
            return []
        wanted = set(task.pick_ids)
        return [p for p in load_snapshot(self.dirs.tasks / task.id) if p.id in wanted]

    def _crops(self, picks: Sequence[Pick], limit: int) -> list[LabelledImage]:
        out: list[LabelledImage] = []
        for pick in picks:
            if len(out) >= limit:
                break
            image = self.uploads.load(pick.screenshots.crop) if pick.screenshots else None
            if image is not None:
                out.append(LabelledImage(f"[{pick.id}] crop", image))
        return out

    def worker_images(self, picks: Sequence[Pick]) -> list[LabelledImage]:
        """Per pick, the crop then the viewport screenshot (viewport only for the first
        few picks); missing uploads are skipped."""
        out: list[LabelledImage] = []
        for i, pick in enumerate(picks):
            if pick.screenshots is None:
                continue
            refs = [("crop", pick.screenshots.crop)]
            if i < MAX_WORKER_PICKS_WITH_VIEWPORT:
                refs.append(("viewport", pick.screenshots.viewport))
            for kind, ref in refs:
                image = self.uploads.load(ref)
                if image is not None and len(out) < MAX_WORKER_IMAGES:
                    out.append(LabelledImage(f"[{pick.id}] {kind}", image))
        return out

    # --- user turns (talker) ---

    def submit_user_text(
        self,
        text: str,
        pick_ids: list[int] | None = None,
        page: PageContext | None = None,
    ) -> asyncio.Task[None]:
        """Record the user's text now and run the talker turn in the background."""
        self._add_transcript("user", text, pick_ids=pick_ids)
        refs = self.picks.resolve(pick_ids or [])
        task = asyncio.create_task(self._user_turn(text, page, refs))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    async def _user_turn(
        self, text: str, page: PageContext | None = None, refs: Sequence[PickRef] = ()
    ) -> None:
        # A new user turn interrupts the talker's current reply (SPEC §9.2).
        if self._talker_busy and self._talker is not None:
            with contextlib.suppress(Exception):
                await self._talker.interrupt()
        async with self._talker_lock:
            await self._run_talker_turn(text, page, refs)

    def _take_notes(self) -> list[str]:
        """Unseen system notes for the next talker turn; the announcer drops what they
        cover, so it isn't spoken again."""
        notes, self._unseen = self._unseen, []
        if notes:
            self.announcer.mark_seen()
        return notes

    async def _run_talker_turn(
        self, text: str, page: PageContext | None = None, refs: Sequence[PickRef] = ()
    ) -> None:
        if page is not None:
            self._last_page = page
        notes = self._take_notes()
        image: Image | None = None
        if page is not None and page.screenshot:
            image = await asyncio.to_thread(self.uploads.load, page.screenshot)
            if image is None:
                log.warning("screenshot %s not found; sending the turn without it", page.screenshot)
        picks = [r for r in refs if isinstance(r, Pick)]
        crops = await asyncio.to_thread(
            self._crops, picks, MAX_TURN_IMAGES - (1 if image is not None else 0)
        )
        prompt = render_talker_turn(
            text, notes, picks=refs, page=page, screenshot_attached=image is not None
        )
        turn_id = _new_id()
        parts: list[str] = []

        def on_delta(delta: str) -> None:
            parts.append(delta)
            self._emit(AgentTextDelta(payload=AgentTextDeltaPayload(turn_id=turn_id, delta=delta)))

        full = await self._invoke_talker(prompt, on_delta, image, crops, notes, page, picks, False)
        self._emit(AgentTextDone(payload=AgentTextDonePayload(turn_id=turn_id)))
        if full is None:
            return
        reply = (full or "".join(parts)).strip()
        if reply:
            self._add_transcript("agent", reply, turn_id=turn_id)

    # --- voice (SPEC §6.7-6.9) ---

    @property
    def talker_busy(self) -> bool:
        return self._talker_busy or self._talker_lock.locked()

    @property
    def call_active(self) -> bool:
        return self._call is not None

    def attach_call(self, call: Call) -> None:
        self._call = call

    def warm_talker(self) -> None:
        """A call connected: connect the talker now rather than on the first turn."""

        async def warm() -> None:
            if self._talker is None:
                self._talker = self._talker_factory(self)
            try:
                await self._talker.warm()
            except Exception:
                log.warning("talker warm-up failed", exc_info=True)

        task = asyncio.create_task(warm())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def detach_call(self, call: Call) -> None:
        if self._call is call:
            self._call = None
            self._call_page = None

    def set_call_page(self, page: PageContext) -> None:
        """call.page: remember the page in view; ignored without an active call."""
        if self._call is not None:
            self._call_page = page

    def _notify_action(self) -> NotifyAction:
        return "open" if self._call is not None else "call"

    def has_announcements(self) -> bool:
        return bool(self.announcer)

    def voice_interrupted(self, heard: str) -> None:
        self._unseen.append(interrupted_note(heard))

    def _voice_page(self, picks: Sequence[Pick]) -> PageContext | None:
        """The latest call.page (with its screenshot), else the page of the first
        pick, else the page of the last typed turn (without its old screenshot)."""
        if self._call_page is not None:
            return self._call_page
        for pick in picks:
            p = pick.page
            return PageContext(url=p.url, route=p.route, title=p.title, viewport=p.viewport)
        if self._last_page is not None:
            return self._last_page.model_copy(update={"screenshot": None})
        return None

    def voice_reply(
        self, text: str, pick_ids: list[int], *, continuation: bool
    ) -> AsyncGenerator[str, None]:
        """The talker's reply to a spoken turn, streamed for TTS. Same client, notes,
        picks and crops as a typed turn; the transcript is the call's job."""
        refs = self.picks.resolve(pick_ids)
        picks = [r for r in refs if isinstance(r, Pick)]
        page = self._voice_page(picks)
        attached = False

        def build(notes: list[str]) -> str:
            return render_talker_turn(
                text,
                notes,
                picks=refs,
                page=page,
                screenshot_attached=attached,
                spoken=True,
                continuation=continuation,
            )

        def on_image(found: bool) -> None:
            nonlocal attached
            attached = found

        return self._voice_stream(build, picks, page, on_image)

    def voice_announcement(self, *, opening: bool) -> AsyncGenerator[str, None] | None:
        """One spoken update for every pending announcement (SPEC §6.7), or None."""
        if not self.announcer:
            return None
        events = self.announcer.take()

        def build(notes: list[str]) -> str:
            return render_announcement(events, notes, opening=opening)

        return self._voice_stream(build, [], None)

    async def _voice_stream(
        self,
        build: Callable[[list[str]], str],
        picks: Sequence[Pick],
        page: PageContext | None,
        on_image: Callable[[bool], None] | None = None,
    ) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        talking = asyncio.Event()

        async def run() -> None:
            try:
                async with self._talker_lock:
                    notes = self._take_notes()
                    image: Image | None = None
                    if on_image is not None and page is not None and page.screenshot:
                        image = await asyncio.to_thread(self.uploads.load, page.screenshot)
                        if image is None:
                            log.warning("call screenshot %s not found", page.screenshot)
                        on_image(image is not None)
                    limit = MAX_TURN_IMAGES - (1 if image is not None else 0)
                    crops = await asyncio.to_thread(self._crops, picks, limit)
                    talking.set()
                    await self._invoke_talker(
                        build(notes), queue.put_nowait, image, crops, notes, page, picks, True
                    )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run(), name="voice-turn")
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        finished = False
        try:
            while (item := await queue.get()) is not None:
                yield item
            finished = True
        finally:
            if not finished and not task.done():
                # Closed early (the user interrupted): stop the talker's turn. It ends
                # with an aborted result about 10 ms later and releases the lock.
                if talking.is_set():
                    stop = asyncio.create_task(self._interrupt_talker())
                    self._bg.add(stop)
                    stop.add_done_callback(self._bg.discard)
                else:
                    task.cancel()

    async def _interrupt_talker(self) -> None:
        if self._talker is not None:
            with contextlib.suppress(Exception):
                await self._talker.interrupt()

    async def _invoke_talker(
        self,
        prompt: str,
        on_delta: OnDelta,
        image: Image | None,
        crops: Sequence[LabelledImage],
        notes: list[str],
        page: PageContext | None,
        picks: Sequence[Pick],
        spoken: bool,
    ) -> str | None:
        """Run one talker turn with the turn's page and picks in scope for dispatch.
        On error: restore the notes, drop the client, record a system entry; None."""
        self._talker_busy = True
        self._turn_page = page
        self._turn_picks = list(picks)
        try:
            if self._talker is None:
                self._talker = self._talker_factory(self)
            return await self._talker.reply(prompt, on_delta, image, crops, spoken=spoken)
        except Exception as exc:
            log.exception("talker turn failed")
            self._unseen = notes + self._unseen
            talker, self._talker = self._talker, None
            if talker is not None:
                with contextlib.suppress(Exception):
                    await talker.close()
            self._add_transcript("system", f"The talker hit an error: {exc}")
            return None
        finally:
            self._talker_busy = False
            self._turn_page = None
            self._turn_picks = []

    # --- talker tools ---

    def dispatch(self, brief: Brief) -> Task:
        """Queue a task from the talker; it carries the page of the current turn."""
        return self._enqueue(brief, self._turn_page, self._dispatch_picks(brief))

    def _dispatch_picks(self, brief: Brief) -> list[Pick]:
        """The brief's target picks plus the current turn's, existing ones only. A pick
        the talker saw this turn wins over the store's copy (ids can be reused)."""
        seen = {p.id: p for p in self._turn_picks}
        ids = [t.pick_id for t in brief.targets if t.pick_id is not None]
        ids += [p.id for p in self._turn_picks]
        out: dict[int, Pick] = {}
        for i in ids:
            pick = seen.get(i) or self.picks.get(i)
            if pick is not None:
                out.setdefault(i, pick)
        return list(out.values())

    def _enqueue(self, brief: Brief, page: PageContext | None, picks: Sequence[Pick] = ()) -> Task:
        brief = normalise_brief(brief, self.repo_path)
        task = Task(
            id=_new_id(),
            title=brief.title,
            brief=brief,
            created_at=_now_ms(),
            pick_ids=[p.id for p in picks],
            page=page.model_copy(deep=True) if page is not None else None,
        )
        if picks:
            # Snapshot now: later picks.clear or id reuse mustn't change a queued task.
            save_snapshot(self.dirs.tasks / task.id, list(picks))
        self.queue.add(task)
        self._emit(TaskCreated(payload=task))
        self._emit_queue()
        self._add_transcript("system", f"Task queued: {task.title}", task_id=task.id)
        self._wake.set()
        return task

    def request_undo(self) -> Task:
        return self._enqueue(UNDO_BRIEF.model_copy(deep=True), None)

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
        task = self.queue.mark_blocked(task_id)
        if task is not None:
            self._emit_queue()
            pending = [a for a in self.approvals.pending() if a.task_id == task_id]
            detail = pending[-1].reason if pending else ""
            self.announcer.add(Announcement("blocked", task_id, task.title, detail, _now_ms()))

    def _on_unblocked(self, task_id: str) -> None:
        self.announcer.resolve_blocked(task_id)
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
            self._announce("completed", done, result.summary)
            self._notify(f"{task.title} done", result.summary or "Done")
        elif result.status == "failed":
            reason = result.error or "unknown error"
            done = self.queue.mark_failed(task.id, reason)
            if done is None:
                return
            self._emit(TaskFailed(payload=TaskFailedPayload(task_id=task.id, reason=reason)))
            self._emit_queue()
            self._add_transcript("system", f"Task failed: {task.title}. {reason}", task_id=task.id)
            self._announce("failed", done, reason)
            self._notify(f"{task.title} failed", reason)
        else:
            done = self.queue.mark_cancelled(task.id)
            if done is None:
                return
            self._emit_queue()
            self._add_transcript("system", f"Task cancelled: {task.title}", task_id=task.id)
            self.announcer.resolve_blocked(task.id)
        self._unseen.append(task_note(done))

    def _announce(self, kind: Literal["completed", "failed"], task: Task, detail: str) -> None:
        self.announcer.add(Announcement(kind, task.id, task.title, detail, _now_ms()))

    def _notify(self, title: str, body: str) -> None:
        """Without a call, a notification whose click starts one (SPEC §6.7). During a
        call the talker speaks the update instead."""
        if self._call is None:
            self._emit(Notify(payload=NotifyPayload(title=title, body=body, action="call")))
