"""One voice call: turns, replies, announcements and transcript plumbing (SPEC §6.7-6.9).

This module has no Pipecat or MLX imports. The pipeline (`voice.pipeline`) reports what
happens through `CallEvents`; `Call` decides what to do:

- User turns: STT segments (with word times) accumulate in a `TurnTracker`. When the
  aggregator ends a turn, the call binds picks and places `[n]` markers
  (`turns.assemble_turn`), records the user entry, and asks the session's talker for a
  spoken reply, which the backend streams into TTS.
- Turn continuation: a turn resumed soon after it ended, before the user heard much of
  the reply, is merged and re-submitted; a turn ending within `PICK_HOLD_MS` of a pick
  is held; a pick that arrives late (slow screenshot upload) but was made during the
  turn re-submits it.
- Replies: the agent's text reaches the rail as it is *spoken* (a sentence at a time as
  playback reaches it), so an interrupted reply is recorded as what the user heard plus
  " …".
- Announcements (SPEC §6.7): a ticker asks `announcer.announcement_due` and, when the
  user has paused, has the talker speak one batched update. A call that starts with
  unannounced events opens with a summary.

Seams: `CallSession` (the repo session: talker, picks, transcript) and `BackendFactory`
(the media side). Tests pass fakes for both.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..announcer import announcement_due
from ..protocol.messages import (
    AgentTextDelta,
    AgentTextDeltaPayload,
    AgentTextDone,
    AgentTextDonePayload,
    CallStateMsg,
    CallStatePayload,
    ServerMessage,
    TranscriptPartial,
    TranscriptPartialPayload,
)
from ..protocol.models import CallState, Role
from .turns import (
    TURN_DEBOUNCE_MS,
    PickTime,
    Segment,
    SubmittedTurn,
    TimedWord,
    TurnTracker,
    assemble_turn,
    turn_hold_until,
)

log = logging.getLogger(__name__)

type ReplyStream = AsyncGenerator[str, None]
"""A talker reply, streamed as text chunks of any size. A newline marks a pause in the
talker's text (a text block ended, a tool call follows): speak what's buffered now
rather than wait for the next sentence. Closing the stream early (an interruption)
stops the talker's turn."""

TICK_SECS = 0.1
OPENING_DELAY_MS = 600.0
"""Let the browser's audio path settle before the talker opens a call."""
PLAYBACK_GRACE_MS = 3000.0
"""Close a reply whose stream ended if playback never reported finishing."""


def daemon_now_ms() -> float:
    return time.time() * 1000.0


async def echo_reply(text: str) -> AsyncGenerator[str, None]:
    """A stub reply: "You said: …", streamed word by word (tests, no Claude)."""
    reply = f"You said: {text.strip()}"
    if not reply.endswith((".", "!", "?")):
        reply += "."
    for word in re.findall(r"\S+\s*", reply):
        yield word


class CallEvents(Protocol):
    """What the media pipeline reports. All callbacks run on the event loop."""

    def connected(self) -> None: ...
    def user_speaking(self) -> None: ...
    def user_stopped(self) -> None: ...
    def user_segment(
        self, text: str, words: Sequence[TimedWord] = (), start: float | None = None
    ) -> None: ...
    def user_turn_ended(self, text: str) -> None: ...
    def interrupted(self) -> None: ...
    def agent_text(self, chunk: str) -> None: ...
    def agent_stream_done(self) -> None: ...
    def agent_sentence(self, text: str) -> None: ...
    def agent_playback_done(self) -> None: ...
    def bot_speaking(self, speaking: bool) -> None: ...
    def latency(self, e2e_ms: float) -> None: ...
    def closed(self) -> None: ...


class CallBackend(Protocol):
    async def start(self, offer_sdp: str) -> str:
        """Accept a complete (non-trickle) SDP offer; return the SDP answer."""
        ...

    def set_muted(self, muted: bool) -> None: ...

    def speak(self, reply: ReplyStream, *, interrupt: bool = False) -> None:
        """Stream a reply into TTS, replacing the current one. `interrupt` also flushes
        audio already queued (used when re-submitting a turn the agent began answering)."""
        ...

    async def stop(self) -> None: ...


BackendFactory = Callable[[CallEvents], CallBackend]


class CallSession(Protocol):
    """The repo session side: talker, picks, announcements and the transcript."""

    def emit(self, msg: ServerMessage) -> None: ...

    def add_transcript(
        self,
        role: Role,
        text: str,
        *,
        turn_id: str | None = None,
        pick_ids: list[int] | None = None,
        replace: bool = False,
    ) -> str:
        """Append (or, with `replace`, update) an entry and broadcast `transcript.final`."""
        ...

    def new_turn_id(self) -> str: ...

    def pick_times(self) -> list[PickTime]: ...

    def voice_reply(self, text: str, pick_ids: list[int], *, continuation: bool) -> ReplyStream:
        """The talker's reply to a spoken turn (serialised with typed turns)."""
        ...

    def voice_announcement(self, *, opening: bool) -> ReplyStream | None:
        """A spoken update covering every pending announcement, or None."""
        ...

    def voice_interrupted(self, heard: str) -> None:
        """Tell the talker (next turn) how much of its reply the user heard."""
        ...

    def has_announcements(self, *, opening: bool = False) -> bool: ...

    def warm_talker(self) -> None:
        """The call connected: get the talker ready for the first turn."""
        ...

    @property
    def talker_busy(self) -> bool: ...

    def attach_call(self, call: Call) -> None: ...

    def detach_call(self, call: Call) -> None: ...


class CallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _AgentTurn:
    turn_id: str
    kind: Literal["reply", "announcement"]
    text: list[str] = field(default_factory=list[str])
    spoken: list[str] = field(default_factory=list[str])
    stream_done_at: float | None = None
    audio_started_at: float | None = None


@dataclass
class _Held:
    segments: list[Segment]
    merged_into: SubmittedTurn | None
    handle: asyncio.TimerHandle
    since: float
    """When the turn ended (call clock, ms)."""


class Call:
    """A call on one connection. Implements `CallEvents` for its backend."""

    def __init__(
        self,
        session: CallSession,
        send: Callable[[ServerMessage], None],
        backend_factory: BackendFactory,
        *,
        on_closed: Callable[[Call], None] | None = None,
        clock: Callable[[], float] = daemon_now_ms,
    ) -> None:
        self.session = session
        self._send = send
        self._on_closed = on_closed
        self._clock = clock
        self.state: CallState = "idle"
        self.muted = False
        self.latencies: list[float] = []
        self.tracker = TurnTracker()
        self.user_turns = 0
        self.announcements = 0
        self.quiet_before_announcements: list[float | None] = []
        """Ms since the user last spoke, at each announcement (None: not yet)."""
        self._partials: list[str] = []
        self._agent: _AgentTurn | None = None
        self._held: _Held | None = None
        self.debounce_ms = TURN_DEBOUNCE_MS
        self.debounced = 0
        """Turns that continued during their hold, so no talker call was made."""
        self._user_speaking = False
        self._bot_speaking = False
        self._last_user_activity: float | None = None
        self._reply_audio_at: float | None = None
        self._connected_at: float | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._offered = False
        self._ended = False
        self._close_task: asyncio.Task[None] | None = None
        session.attach_call(self)
        self.backend = backend_factory(self)
        self._set_state("connecting")

    @property
    def ended(self) -> bool:
        return self._ended

    def _set_state(self, state: CallState) -> None:
        if state != self.state:
            self.state = state
            self._send(CallStateMsg(payload=CallStatePayload(state=state)))

    def _settle_state(self) -> None:
        """listening / thinking / speaking from what's going on."""
        if self._ended:
            return
        agent = self._agent
        if self._bot_speaking:
            self._set_state("speaking")
        elif self._held is not None or (agent is not None and agent.audio_started_at is None):
            self._set_state("thinking")
        elif self.state != "connecting":
            self._set_state("listening")

    # --- control (from the connection) ---

    async def offer(self, sdp: str) -> str:
        if self._ended:
            raise CallError("no_call", "The call has ended.")
        if self._offered:
            raise CallError("call_active", "This call already has a WebRTC connection.")
        self._offered = True
        return await self.backend.start(sdp)

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.backend.set_muted(muted)

    async def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        self._finish_agent(interrupted=True)
        self._drop_hold()
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None
        self.session.detach_call(self)
        try:
            await self.backend.stop()
        except Exception:  # pragma: no cover - teardown is best effort
            log.exception("voice backend stop failed")
        self._set_state("idle")
        if self._on_closed is not None:
            self._on_closed(self)

    # --- CallEvents: connection ---

    def connected(self) -> None:
        if self._ended:
            return
        self._connected_at = self._clock()
        self._set_state("listening")
        self.session.warm_talker()
        if self._ticker is None:
            self._ticker = asyncio.get_running_loop().create_task(self._tick_loop())

    def closed(self) -> None:
        """The peer went away (ICE failure, tab closed): tear down like call.end."""
        if self._ended:
            return
        if self._close_task is None:
            self._close_task = asyncio.get_running_loop().create_task(self.end())

    def latency(self, e2e_ms: float) -> None:
        self.latencies.append(e2e_ms)

    # --- CallEvents: the user ---

    def user_speaking(self) -> None:
        if self._ended:
            return
        self._speech_started()
        self._send(TranscriptPartial(payload=TranscriptPartialPayload(text=self._partial("…"))))

    def user_stopped(self) -> None:
        if self._ended:
            return
        self._user_speaking = False
        self._last_user_activity = self._clock()
        self.tracker.speech_stopped()

    def user_segment(
        self, text: str, words: Sequence[TimedWord] = (), start: float | None = None
    ) -> None:
        if self._ended or not text.strip():
            return
        self._last_user_activity = self._clock()
        self.tracker.add_segment(Segment(text.strip(), tuple(words), start))
        self._partials.append(text.strip())
        self._send(TranscriptPartial(payload=TranscriptPartialPayload(text=self._partial())))

    def user_turn_ended(self, text: str) -> None:
        """The aggregator ended the turn (Smart Turn + transcript). `text` is its own
        aggregation, used only if no STT segment reached the tracker."""
        if self._ended:
            return
        segments, merged_into = self.tracker.take()
        self._partials = []
        if not segments:
            if not text.strip():
                return
            segments = [Segment(text.strip())]
        now = self._clock()
        self._last_user_activity = now
        until = turn_hold_until(now, self.session.pick_times(), self.debounce_ms)
        if until is not None:
            # Wait a moment before answering: Smart Turn sometimes ends a turn
            # mid-sentence, and after a pick the user may still be pointing. Speech in
            # the meantime continues this turn, with no talker call made.
            delay = (until - now) / 1000.0
            handle = asyncio.get_running_loop().call_later(delay, self._release_hold)
            self._held = _Held(segments, merged_into, handle, now)
            self._settle_state()
            return
        self._submit(segments, merged_into, now)

    def interrupted(self) -> None:
        """An InterruptionFrame reached the talker: the user started speaking."""
        if self._ended:
            return
        self._speech_started()

    # --- CallEvents: the agent ---

    def agent_text(self, chunk: str) -> None:
        if self._agent is not None:
            self._agent.text.append(chunk)

    def agent_stream_done(self) -> None:
        agent = self._agent
        if agent is None:
            return
        agent.stream_done_at = self._clock()
        if not "".join(agent.text).strip():
            self._finish_agent(interrupted=False)

    def agent_sentence(self, text: str) -> None:
        """A sentence of the reply starts playing."""
        agent = self._agent
        if agent is None or self._ended or not text.strip():
            return
        if agent.audio_started_at is None:
            agent.audio_started_at = self._clock()
            if agent.kind == "reply":
                self._reply_audio_at = agent.audio_started_at
        delta = (" " if agent.spoken else "") + text.strip()
        agent.spoken.append(text.strip())
        self.session.emit(
            AgentTextDelta(payload=AgentTextDeltaPayload(turn_id=agent.turn_id, delta=delta))
        )

    def agent_playback_done(self) -> None:
        agent = self._agent
        if agent is not None and agent.stream_done_at is not None:
            self._finish_agent(interrupted=False)

    def bot_speaking(self, speaking: bool) -> None:
        if self._ended:
            return
        self._bot_speaking = speaking
        self._settle_state()

    # --- from the session ---

    def pick_added(self, pick: PickTime) -> None:
        """A pick reached the daemon. If it was made during the last turn and the user
        has heard little of the reply, re-submit that turn with it."""
        if self._ended or self._held is not None:
            return
        last = self.tracker.last
        now = self._clock()
        if last is None or not self.tracker.late_pick_fits(pick, now, self._reply_audio_at):
            return
        log.info("late pick [%d]: re-submitting the turn", pick.id)
        self._submit(list(last.segments), last, now, interrupt=True)

    # --- internals ---

    def _speech_started(self) -> None:
        if self._user_speaking:
            return
        self._user_speaking = True
        now = self._clock()
        self._last_user_activity = now
        held = self._held
        if held is not None:
            # The user resumed while we were holding the turn: it continues.
            held.handle.cancel()
            self._held = None
            self.debounced += 1
            log.info(
                "turn debounced: speech resumed %.0f ms after the turn ended (window %.0f ms)",
                now - held.since,
                self.debounce_ms,
            )
            merged = held.merged_into
            new = held.segments[len(merged.segments) :] if merged else held.segments
            self.tracker.put_back(new, continuation=merged is not None)
        self.tracker.speech_started(now, self._reply_audio_at)
        self._finish_agent(interrupted=True)
        self._settle_state()

    def _release_hold(self) -> None:
        held, self._held = self._held, None
        if held is None or self._ended:
            return
        self._submit(held.segments, held.merged_into, self._clock())

    def _drop_hold(self) -> None:
        held, self._held = self._held, None
        if held is not None:
            held.handle.cancel()

    def _submit(
        self,
        segments: list[Segment],
        merged_into: SubmittedTurn | None,
        now: float,
        *,
        interrupt: bool = False,
    ) -> None:
        turn = assemble_turn(segments, self.session.pick_times(), now)
        turn_id = merged_into.turn_id if merged_into is not None else self.session.new_turn_id()
        pick_ids = list(turn.pick_ids)
        self._finish_agent(interrupted=True)
        self.session.add_transcript(
            "user", turn.text, turn_id=turn_id, pick_ids=pick_ids, replace=merged_into is not None
        )
        self.tracker.submitted(
            SubmittedTurn(turn_id, list(segments), now, turn.window, turn.pick_ids, turn.text)
        )
        self.user_turns += 1
        self._reply_audio_at = None
        log.info("voice turn%s: %s", " (continued)" if merged_into else "", turn.text)
        log.debug(
            "turn words %s; picks %s",
            [(w.text, round(w.end)) for s in segments for w in s.words],
            [(p.id, round(p.created_at)) for p in self.session.pick_times()],
        )
        stream = self.session.voice_reply(turn.text, pick_ids, continuation=merged_into is not None)
        self._start_agent(stream, "reply", interrupt=interrupt)

    def _start_agent(
        self, stream: ReplyStream, kind: Literal["reply", "announcement"], *, interrupt: bool
    ) -> None:
        self._finish_agent(interrupted=True)
        self._agent = _AgentTurn(self.session.new_turn_id(), kind)
        self.backend.speak(stream, interrupt=interrupt)
        self._settle_state()

    def _finish_agent(self, *, interrupted: bool) -> None:
        """Close the agent's turn. Played to the end: record the full reply. Cut off:
        record only what the user heard, plus " …", and tell the talker."""
        agent, self._agent = self._agent, None
        if agent is None:
            return
        full = " ".join("".join(agent.text).split())
        spoken = " ".join(agent.spoken).strip()
        if agent.spoken:
            self.session.emit(AgentTextDone(payload=AgentTextDonePayload(turn_id=agent.turn_id)))
        if not interrupted:
            text = full or spoken
        else:
            text = f"{spoken} …" if spoken else ""
            if full:
                self.session.voice_interrupted(spoken)
        if text:
            self.session.add_transcript("agent", text, turn_id=agent.turn_id)
        self._settle_state()

    def _partial(self, tail: str = "") -> str:
        return " ".join([*self._partials, tail]).strip()

    async def _tick_loop(self) -> None:
        while not self._ended:
            await asyncio.sleep(TICK_SECS)
            with contextlib.suppress(Exception):
                self._tick()

    def _tick(self) -> None:
        now = self._clock()
        agent = self._agent
        if (
            agent is not None
            and agent.stream_done_at is not None
            and not self._bot_speaking
            and now - agent.stream_done_at > PLAYBACK_GRACE_MS
        ):
            self._finish_agent(interrupted=False)
        if self._connected_at is None or now - self._connected_at < OPENING_DELAY_MS:
            return
        busy = (
            self._agent is not None
            or self._held is not None
            or self._bot_speaking
            or bool(self.tracker.open)
            or self.session.talker_busy
        )
        opening = self.user_turns == 0 and self.announcements == 0
        if not announcement_due(
            now,
            pending=self.session.has_announcements(opening=opening),
            user_speaking=self._user_speaking,
            agent_busy=busy,
            last_user_activity=self._last_user_activity,
        ):
            return
        stream = self.session.voice_announcement(opening=opening)
        if stream is None:
            return
        self.announcements += 1
        last = self._last_user_activity
        self.quiet_before_announcements.append(None if last is None else now - last)
        log.info("voice announcement (opening=%s)", opening)
        self._start_agent(stream, "announcement", interrupt=False)
