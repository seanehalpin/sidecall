"""One voice call: state machine, transcript plumbing and seams (SPEC §6.8, §9).

This module has no Pipecat or MLX imports. The pipeline (`voice.pipeline`) reports what
happens through `CallEvents`; `Call` turns that into `call.state`, `transcript.*` and
`agent.text.*` messages and repo transcript entries.

Seams for Phase 2:
- `TalkerFn`: `(text) -> async stream of text`. The stub echoes; the real talker plugs in
  here (the turn assembler hands it marked text).
- `BackendFactory`: builds the media side. Tests pass a fake; the daemon uses
  `voice.pipeline.PipecatBackend`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Protocol

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

log = logging.getLogger(__name__)

TalkerFn = Callable[[str], AsyncIterator[str]]
"""The talker seam: user turn text in, reply text streamed out (any chunking)."""


async def echo_talker(text: str) -> AsyncIterator[str]:
    """Phase 1 stub talker: "You said: …", streamed word by word."""
    reply = f"You said: {text.strip()}"
    if not reply.endswith((".", "!", "?")):
        reply += "."
    for word in re.findall(r"\S+\s*", reply):
        yield word


class CallEvents(Protocol):
    """What the media pipeline reports. All callbacks run on the event loop."""

    def connected(self) -> None: ...
    def user_speaking(self) -> None: ...
    def user_segment(self, text: str) -> None: ...
    def user_turn(self, text: str) -> None: ...
    def agent_delta(self, delta: str) -> None: ...
    def agent_done(self, interrupted: bool) -> None: ...
    def bot_speaking(self, speaking: bool) -> None: ...
    def interrupted(self) -> None: ...
    def latency(self, e2e_ms: float) -> None: ...
    def closed(self) -> None: ...


class CallBackend(Protocol):
    async def start(self, offer_sdp: str) -> str:
        """Accept a complete (non-trickle) SDP offer; return the SDP answer."""
        ...

    def set_muted(self, muted: bool) -> None: ...

    async def stop(self) -> None: ...


BackendFactory = Callable[[CallEvents, TalkerFn], CallBackend]


class CallSession(Protocol):
    """The repo session side: broadcasts and the persisted transcript."""

    def emit(self, msg: ServerMessage) -> None: ...

    def add_transcript(self, role: Role, text: str, *, turn_id: str | None = None) -> str:
        """Append a transcript entry and broadcast `transcript.final`; returns its turnId."""
        ...

    def new_turn_id(self) -> str: ...


class CallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Call:
    """A call on one connection. Implements `CallEvents` for its backend."""

    def __init__(
        self,
        session: CallSession,
        send: Callable[[ServerMessage], None],
        backend_factory: BackendFactory,
        talker: TalkerFn,
        *,
        on_closed: Callable[[Call], None] | None = None,
    ) -> None:
        self.session = session
        self._send = send
        self._on_closed = on_closed
        self.state: CallState = "idle"
        self.muted = False
        self.latencies: list[float] = []
        self._segments: list[str] = []
        self._agent_turn: str | None = None
        self._agent_text: list[str] = []
        self._offered = False
        self._ended = False
        self._close_task: asyncio.Task[None] | None = None
        self.backend = backend_factory(self, talker)
        self._set_state("connecting")

    @property
    def ended(self) -> bool:
        return self._ended

    def _set_state(self, state: CallState) -> None:
        if state != self.state:
            self.state = state
            self._send(CallStateMsg(payload=CallStatePayload(state=state)))

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
        try:
            await self.backend.stop()
        except Exception:  # pragma: no cover - teardown is best effort
            log.exception("voice backend stop failed")
        self._set_state("idle")
        if self._on_closed is not None:
            self._on_closed(self)

    # --- CallEvents (from the backend) ---

    def connected(self) -> None:
        if not self._ended:
            self._set_state("listening")

    def user_speaking(self) -> None:
        if self._ended:
            return
        self._send(TranscriptPartial(payload=TranscriptPartialPayload(text=self._partial("…"))))

    def user_segment(self, text: str) -> None:
        if self._ended or not text.strip():
            return
        self._segments.append(text.strip())
        self._send(TranscriptPartial(payload=TranscriptPartialPayload(text=self._partial())))

    def user_turn(self, text: str) -> None:
        if self._ended:
            return
        self._segments.clear()
        self._finish_agent(interrupted=True)
        self.session.add_transcript("user", text.strip())
        self._agent_turn = self.session.new_turn_id()
        self._agent_text = []
        self._set_state("thinking")

    def agent_delta(self, delta: str) -> None:
        if self._ended or self._agent_turn is None or not delta:
            return
        self._agent_text.append(delta)
        self.session.emit(
            AgentTextDelta(payload=AgentTextDeltaPayload(turn_id=self._agent_turn, delta=delta))
        )

    def agent_done(self, interrupted: bool) -> None:
        if self._ended:
            return
        had_text = bool("".join(self._agent_text).strip())
        self._finish_agent(interrupted=interrupted)
        if not had_text and self.state == "thinking":
            self._set_state("listening")

    def bot_speaking(self, speaking: bool) -> None:
        if self._ended:
            return
        if speaking:
            self._set_state("speaking")
        elif self.state == "speaking":
            self._set_state("listening")

    def interrupted(self) -> None:
        if self._ended:
            return
        self._finish_agent(interrupted=True)
        self._set_state("listening")

    def latency(self, e2e_ms: float) -> None:
        self.latencies.append(e2e_ms)

    def closed(self) -> None:
        """The peer went away (ICE failure, tab closed): tear down like call.end."""
        if self._ended:
            return
        if self._close_task is None:
            self._close_task = asyncio.get_running_loop().create_task(self.end())

    # --- helpers ---

    def _partial(self, tail: str = "") -> str:
        return " ".join([*self._segments, tail]).strip() if tail else " ".join(self._segments)

    def _finish_agent(self, *, interrupted: bool) -> None:
        turn_id = self._agent_turn
        if turn_id is None:
            return
        self._agent_turn = None
        text = "".join(self._agent_text).strip()
        self._agent_text = []
        self.session.emit(AgentTextDone(payload=AgentTextDonePayload(turn_id=turn_id)))
        if text:
            if interrupted:
                text += " …"
            self.session.add_transcript("agent", text, turn_id=turn_id)
