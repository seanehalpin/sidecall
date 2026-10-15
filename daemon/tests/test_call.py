"""Voice calls without models: the Call state machine (turns, continuation, late picks,
interruptions, announcements) with a fake backend and session, plus signalling over the
WebSocket with the real RepoSession."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from helpers import make_brief, make_pick
from starlette.testclient import TestClient
from test_server import (
    APP,
    APP2,
    FakeTalker,
    FakeWorker,
    Msg,
    bound,
    hello,
    send,
    until,
    upload,
)

from sidecall.announcer import QUIET_MS
from sidecall.config import Config
from sidecall.protocol.messages import ServerMessage
from sidecall.protocol.models import Role
from sidecall.server import Daemon, create_app
from sidecall.voice.call import (
    OPENING_DELAY_MS,
    Call,
    CallError,
    CallEvents,
    ReplyStream,
    echo_reply,
)
from sidecall.voice.turns import PickTime, TimedWord

T0 = 1_700_000_000_000.0


async def script(text: str) -> AsyncGenerator[str, None]:
    """A reply streamed verbatim, word by word."""
    for word in re.findall(r"\S+\s*", text):
        yield word


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


class FakeBackend:
    """Records replies; `play()` acts out TTS + playback for the latest one."""

    instances: list[FakeBackend] = []  # noqa: RUF012

    def __init__(self, events: CallEvents, *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.offers: list[str] = []
        self.muted: list[bool] = []
        self.replies: list[tuple[ReplyStream, bool]] = []
        self.stopped = 0
        FakeBackend.instances.append(self)

    async def start(self, offer_sdp: str) -> str:
        if self.fail:
            raise RuntimeError("no mic")
        self.offers.append(offer_sdp)
        self.events.connected()
        return f"answer for {offer_sdp}"

    def set_muted(self, muted: bool) -> None:
        self.muted.append(muted)

    def speak(self, reply: ReplyStream, *, interrupt: bool = False) -> None:
        self.replies.append((reply, interrupt))

    async def stop(self) -> None:
        self.stopped += 1

    async def stream(self, index: int = -1) -> str:
        """Run the reply's text through (as the TalkerProcessor would), unplayed."""
        reply, _ = self.replies[index]
        text: list[str] = []
        async for chunk in reply:
            text.append(chunk)
            self.events.agent_text(chunk)
        self.events.agent_stream_done()
        return "".join(text)

    async def play(self, index: int = -1, *, upto: int | None = None) -> str:
        """Stream and play the reply; `upto` stops after that many sentences."""
        text = await self.stream(index)
        self.events.bot_speaking(True)
        parts = sentences(text)
        for s in parts[:upto]:
            self.events.agent_sentence(s)
        if upto is None:
            self.events.agent_playback_done()
            self.events.bot_speaking(False)
        return text

    async def say(self, text: str) -> None:
        """One full voice turn through the pipeline's events (no word timings)."""
        ev = self.events
        ev.user_speaking()
        ev.user_stopped()
        ev.user_segment(text)
        ev.user_turn_ended(text)
        await asyncio.sleep(0)
        await self.play()
        ev.latency(512.0)


class FakeSession:
    def __init__(self) -> None:
        self.emitted: list[ServerMessage] = []
        self.entries: list[tuple[Role, str, str]] = []
        self.user_picks: list[list[int]] = []
        self.replaced: list[str] = []
        self.requests: list[tuple[str, list[int], bool]] = []
        self.picks: list[PickTime] = []
        self.pending_announcements = 0
        self.announced: list[bool] = []
        self.interruptions: list[str] = []
        self.busy = False
        self.warmed = 0
        self.attached: object | None = None
        self.reply_text: Callable[[str], AsyncGenerator[str, None]] = echo_reply
        self._n = 0

    def emit(self, msg: ServerMessage) -> None:
        self.emitted.append(msg)

    def new_turn_id(self) -> str:
        self._n += 1
        return f"turn{self._n}"

    def add_transcript(
        self,
        role: Role,
        text: str,
        *,
        turn_id: str | None = None,
        pick_ids: list[int] | None = None,
        replace: bool = False,
    ) -> str:
        turn_id = turn_id or self.new_turn_id()
        if replace:
            self.replaced.append(turn_id)
            self.entries = [e for e in self.entries if not (e[0] == role and e[2] == turn_id)]
        self.entries.append((role, text, turn_id))
        if role == "user":
            self.user_picks.append(list(pick_ids or []))
        return turn_id

    def pick_times(self) -> list[PickTime]:
        return list(self.picks)

    def voice_reply(self, text: str, pick_ids: list[int], *, continuation: bool) -> ReplyStream:
        self.requests.append((text, pick_ids, continuation))
        return self.reply_text(text)

    def voice_announcement(self, *, opening: bool) -> ReplyStream | None:
        if not self.pending_announcements:
            return None
        self.pending_announcements = 0
        self.announced.append(opening)

        async def update() -> AsyncGenerator[str, None]:
            yield "Card spacing is done."

        return update()

    def voice_interrupted(self, heard: str) -> None:
        self.interruptions.append(heard)

    def has_announcements(self, *, opening: bool = False) -> bool:
        return self.pending_announcements > 0

    def warm_talker(self) -> None:
        self.warmed += 1

    @property
    def talker_busy(self) -> bool:
        return self.busy

    def attach_call(self, call: Call) -> None:
        self.attached = call

    def detach_call(self, call: Call) -> None:
        if self.attached is call:
            self.attached = None


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def make_call() -> tuple[Call, FakeSession, list[ServerMessage], Clock]:
    sent: list[ServerMessage] = []
    session = FakeSession()
    clock = Clock()
    call = Call(session, sent.append, FakeBackend, clock=clock)
    return call, session, sent, clock


def backend_of(call: Call) -> FakeBackend:
    assert isinstance(call.backend, FakeBackend)
    return call.backend


def states(sent: list[ServerMessage]) -> list[str]:
    return [m.payload.state for m in sent if m.type == "call.state"]


def words(spec: str, start: float, step: float = 400.0) -> list[TimedWord]:
    return [
        TimedWord(w, start + i * step, start + i * step + 300) for i, w in enumerate(spec.split())
    ]


def speak(call: Call, clock: Clock, text: str, start: float) -> None:
    """The user says `text` starting at `start` (words 400 ms apart); ends the turn."""
    ws = words(text, start)
    clock.now = start
    call.user_speaking()
    clock.now = ws[-1].end + 200
    call.user_stopped()
    call.user_segment(text, ws, start)
    clock.now = ws[-1].end + 300
    call.user_turn_ended(text)


def user_texts(session: FakeSession) -> list[str]:
    return [t for r, t, _ in session.entries if r == "user"]


def agent_texts(session: FakeSession) -> list[str]:
    return [t for r, t, _ in session.entries if r == "agent"]


# --- Call state machine ----------------------------------------------------------------


async def test_echo_reply_streams_words() -> None:
    chunks = [c async for c in echo_reply(" make it red ")]
    assert "".join(chunks) == "You said: make it red."
    assert len(chunks) == 5


async def test_happy_path_states_transcript_and_spoken_deltas() -> None:
    call, session, sent, _ = make_call()
    backend = backend_of(call)
    assert session.attached is call
    assert await call.offer("v=0 offer") == "answer for v=0 offer"
    assert session.warmed == 1
    await backend.say("make it red")
    assert states(sent) == ["connecting", "listening", "thinking", "speaking", "listening"]
    partials = [m.payload.text for m in sent if m.type == "transcript.partial"]
    assert partials == ["…", "make it red"]
    assert [(r, t) for r, t, _ in session.entries] == [
        ("user", "make it red"),
        ("agent", "You said: make it red."),
    ]
    assert session.requests == [("make it red", [], False)]
    agent_turn = session.entries[1][2]
    deltas = [m for m in session.emitted if m.type == "agent.text.delta"]
    assert [m.payload.delta for m in deltas] == ["You said: make it red."]
    assert all(m.payload.turn_id == agent_turn for m in deltas)
    assert session.emitted[-1].type == "agent.text.done"
    assert call.latencies == [512.0]
    await call.end()
    assert states(sent)[-1] == "idle"
    assert backend.stopped == 1
    assert session.attached is None
    await call.end()  # idempotent
    assert backend.stopped == 1


async def test_markers_in_spoken_turn() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    start = T0 + 10_000
    # make@0 this@400-700 match@800 that@1200-1500
    session.picks = [PickTime(1, start + 750), PickTime(2, start - 5000)]
    speak(call, clock, "make this match that", start)
    assert user_texts(session) == ["make this [1] match that"]
    assert session.user_picks == [[1]]  # [2] is outside the window and stays for later
    assert session.requests[-1] == ("make this [1] match that", [1], False)


async def test_interruption_records_only_what_was_heard() -> None:
    call, session, sent, clock = make_call()
    session.reply_text = lambda _t: script("Once upon a time. There was a card. The end")
    await call.offer("sdp")
    speak(call, clock, "tell me a story", T0)
    await backend_of(call).play(upto=1)
    assert states(sent)[-1] == "speaking"
    clock.now += 3000
    call.interrupted()  # the user barged in
    call.bot_speaking(False)
    assert agent_texts(session) == ["Once upon a time. …"]
    assert session.interruptions == ["Once upon a time."]
    assert states(sent)[-1] == "listening"
    # A second interruption doesn't record anything twice.
    call.interrupted()
    assert len(agent_texts(session)) == 1


async def test_interruption_before_audio_records_nothing() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    speak(call, clock, "hello", T0)
    await backend_of(call).stream()  # text arrived, nothing played yet
    clock.now += 4000
    call.user_speaking()
    assert agent_texts(session) == []
    assert session.interruptions == [""]
    assert not [m for m in session.emitted if m.type.startswith("agent.text")]


async def test_empty_reply_returns_to_listening() -> None:
    call, session, sent, clock = make_call()

    async def silent(_text: str) -> AsyncGenerator[str, None]:
        if False:
            yield ""

    session.reply_text = silent
    await call.offer("sdp")
    speak(call, clock, "hi", T0)
    await backend_of(call).stream()
    assert states(sent)[-2:] == ["thinking", "listening"]
    assert agent_texts(session) == []


async def test_quick_resume_merges_into_previous_turn() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    session.picks = [PickTime(1, T0 - 500)]
    speak(call, clock, "make this", T0)  # ends at T0 + 1000
    first_turn = session.entries[0][2]
    assert session.requests == [("[1] make this", [1], False)]
    # 0.8 s later the user carries on; the reply hadn't started playing.
    speak(call, clock, "match the sidebar", T0 + 1800)
    assert session.replaced == [first_turn]
    assert user_texts(session) == ["[1] make this match the sidebar"]
    assert session.entries[-1][2] == first_turn
    assert session.requests[-1] == ("[1] make this match the sidebar", [1], True)


async def test_resume_after_long_gap_is_a_new_turn() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    speak(call, clock, "make it red", T0)
    speak(call, clock, "and bigger", T0 + 4000)
    assert user_texts(session) == ["make it red", "and bigger"]
    assert session.replaced == []
    assert [c for _t, _p, c in session.requests] == [False, False]


async def test_resume_after_hearing_the_reply_is_a_new_turn() -> None:
    call, session, _sent, clock = make_call()
    session.reply_text = lambda _t: script("Sure thing. I'll queue that now.")
    await call.offer("sdp")
    speak(call, clock, "make it red", T0)  # ends at T0 + 1500
    clock.now = T0 + 1600
    await backend_of(call).play(upto=1)  # audio starts at T0 + 1600
    speak(call, clock, "wait", T0 + 2900)  # 1.3 s of reply heard
    assert user_texts(session) == ["make it red", "wait"]
    assert agent_texts(session) == ["Sure thing. …"]


async def test_pick_hold_delays_submission_then_releases() -> None:
    call, session, sent, clock = make_call()
    await call.offer("sdp")
    # The turn ends at T0 + 1000. A pick at T0 + 20 holds it until T0 + 1020.
    session.picks = [PickTime(1, T0 + 20)]
    speak(call, clock, "make this", T0)
    assert session.requests == []
    assert states(sent)[-1] == "thinking"
    clock.now += 30
    await asyncio.sleep(0.06)
    assert session.requests == [("[1] make this", [1], False)]


async def test_speech_during_hold_continues_the_turn() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    session.picks = [PickTime(1, T0 + 900)]
    speak(call, clock, "make this", T0)  # held for ~900 ms
    assert session.requests == []
    speak(call, clock, "bigger", T0 + 1400)  # resumes during the hold, ends at T0 + 2000
    await asyncio.sleep(0.02)
    assert user_texts(session) == ["make this [1] bigger"]
    assert session.replaced == []


async def test_late_pick_resubmits_turn() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    speak(call, clock, "make this red", T0)  # ends at T0 + 1100
    turn_id = session.entries[0][2]
    assert session.requests == [("make this red", [], False)]
    clock.now = T0 + 1500
    session.picks = [PickTime(3, T0 + 750)]
    call.pick_added(PickTime(3, T0 + 750))
    assert session.replaced == [turn_id]
    assert user_texts(session) == ["make this [3] red"]
    assert session.requests[-1] == ("make this [3] red", [3], True)
    assert backend_of(call).replies[-1][1] is True  # flushes what TTS already had
    # A pick made well after the turn doesn't.
    call.pick_added(PickTime(4, T0 + 60_000))
    assert len(session.requests) == 2


async def test_announcement_waits_for_pause_and_idle_agent() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    clock.now = T0 + 5000
    speak(call, clock, "make it red", T0 + 5000)
    session.pending_announcements = 1
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == []  # the agent is replying
    await backend_of(call).play()
    clock.now += QUIET_MS - 500
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == []  # the user spoke under 1.2 s ago
    session.busy = True
    clock.now += 1000
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == []  # a typed turn holds the talker
    session.busy = False
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == [False]
    assert call.quiet_before_announcements == [QUIET_MS + 500]
    await backend_of(call).play()
    assert agent_texts(session)[-1] == "Card spacing is done."
    assert call.announcements == 1


async def test_call_opens_with_summary_of_pending_events() -> None:
    call, session, _sent, clock = make_call()
    session.pending_announcements = 2
    await call.offer("sdp")
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == []  # let the audio path settle first
    clock.now += OPENING_DELAY_MS
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == [True]
    assert user_texts(session) == []


async def test_user_speaking_blocks_announcement() -> None:
    call, session, _sent, clock = make_call()
    await call.offer("sdp")
    clock.now += 10_000
    call.user_speaking()
    session.pending_announcements = 1
    clock.now += 5000
    call._tick()  # pyright: ignore[reportPrivateUsage]
    assert session.announced == []


async def test_end_flushes_open_reply_and_ignores_late_events() -> None:
    call, session, sent, clock = make_call()
    session.reply_text = lambda _t: script("You said hello. Nice.")
    await call.offer("sdp")
    speak(call, clock, "hello", T0)
    await backend_of(call).play(upto=1)
    await call.end()
    assert agent_texts(session) == ["You said hello. …"]
    n = len(sent)
    call.bot_speaking(True)
    call.user_turn_ended("late")
    assert len(sent) == n
    assert len(session.entries) == 2


async def test_second_offer_and_offer_after_end_are_errors() -> None:
    call, *_ = make_call()
    await call.offer("sdp")
    with pytest.raises(CallError) as exc:
        await call.offer("again")
    assert exc.value.code == "call_active"
    await call.end()
    with pytest.raises(CallError) as exc:
        await call.offer("again")
    assert exc.value.code == "no_call"


async def test_peer_close_ends_call() -> None:
    closed: list[Call] = []
    sent: list[ServerMessage] = []
    call = Call(FakeSession(), sent.append, FakeBackend, on_closed=closed.append)
    await call.offer("sdp")
    call.closed()
    await asyncio.sleep(0)
    assert call.ended and closed == [call]
    assert states(sent)[-1] == "idle"


def test_mute_reaches_backend() -> None:
    sent: list[ServerMessage] = []
    call = Call(FakeSession(), sent.append, FakeBackend)
    call.set_muted(True)
    call.set_muted(False)
    assert backend_of(call).muted == [True, False]


# --- the real RepoSession behind a call ------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    return path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.load(tmp_path / "home")


@pytest.fixture
def daemon(config: Config) -> Daemon:
    FakeBackend.instances.clear()
    FakeTalker.instances.clear()

    async def browse(origin: str) -> str | None:
        return None

    return Daemon(
        config,
        talker_factory=FakeTalker,
        worker_factory=FakeWorker,
        browse=browse,
        ping_interval=3600,
        call_backend_factory=FakeBackend,
    )


@pytest.fixture
def client(daemon: Daemon) -> Iterator[TestClient]:
    with TestClient(create_app(daemon)) as c:
        yield c


def _state(state: str) -> Callable[[Msg], bool]:
    def pred(m: Msg) -> bool:
        return m["payload"]["state"] == state

    return pred


def _role(role: str) -> Callable[[Msg], bool]:
    def pred(m: Msg) -> bool:
        return m["payload"]["role"] == role

    return pred


def test_call_signalling_and_voice_turn_through_the_talker(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "call.start")
        assert until(ws, "call.state")[0]["payload"]["state"] == "connecting"
        send(ws, "webrtc.offer", {"sdp": "v=0 browser"})
        _msg, seen = until(ws, "webrtc.answer")
        assert seen[-1]["payload"]["sdp"] == "answer for v=0 browser"
        assert any(m["type"] == "call.state" and m["payload"]["state"] == "listening" for m in seen)
        send(ws, "call.start")
        assert until(ws, "error")[0]["payload"]["code"] == "call_active"
        send(ws, "call.mute", {"muted": True})

        backend = FakeBackend.instances[-1]
        assert backend.offers == ["v=0 browser"]

        assert client.portal is not None
        client.portal.call(backend.say, "chat")
        user, seen = until(ws, "transcript.final")
        assert user["payload"]["role"] == "user"
        assert user["payload"]["text"] == "chat"
        agent, seen2 = until(ws, "transcript.final")
        assert agent["payload"]["text"] == "Hello there."
        types = [m["type"] for m in seen + seen2]
        assert "agent.text.delta" in types and "agent.text.done" in types
        assert backend.muted == [True]

        talker = FakeTalker.instances[-1]
        assert talker.spoken == [True]
        assert talker.warmed == 1
        # the call talker's turn: status line, no history yet (this is the first turn)
        assert talker.prompts[-1] == "[Worker] no tasks yet.\nUser: chat"

        session = daemon.sessions[str(repo.resolve())]
        assert [(e.role, e.text) for e in session.transcript.recent()] == [
            ("user", "chat"),
            ("agent", "Hello there."),
        ]
        send(ws, "call.end")
        until(ws, "call.state", _state("idle"))
        assert backend.stopped == 1
        assert not session.call_active
    finally:
        ws.__exit__(None, None, None)


def test_spoken_turn_uses_latest_call_page(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    ws = bound(client, config, repo)
    try:
        # call.page without a call is ignored silently.
        page = {
            "url": f"{APP}/a",
            "route": "/a",
            "title": "A",
            "viewport": {"w": 800, "h": 600, "dpr": 1},
        }
        send(ws, "call.page", {"page": page})
        send(ws, "call.start")
        until(ws, "call.state")
        send(ws, "webrtc.offer", {"sdp": "v=0 browser"})
        until(ws, "webrtc.answer")
        ref = upload(client, config.token).json()["ref"]
        send(ws, "call.page", {"page": {**page, "screenshot": None}})
        send(
            ws, "call.page", {"page": {**page, "url": f"{APP}/b", "route": "/b", "screenshot": ref}}
        )
        assert client.portal is not None
        backend = FakeBackend.instances[-1]
        client.portal.call(backend.say, "chat")
        until(ws, "transcript.final", _role("agent"))
        talker = FakeTalker.instances[-1]
        assert "/b" in talker.prompts[-1] and "/a\n" not in talker.prompts[-1]
        # no images go to the call talker; the page (with its screenshot) goes to tasks
        assert "screenshot" not in talker.prompts[-1]
        assert talker.images[-1] is None
        send(ws, "call.end")
        until(ws, "call.state", _state("idle"))
    finally:
        ws.__exit__(None, None, None)


def test_voice_turn_binds_picks_and_dispatches(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    (repo / "src" / "lib").mkdir(parents=True)
    (repo / "src" / "lib" / "Card.svelte").write_text("<div class='card-header'></div>\n")
    ws = bound(client, config, repo)
    try:
        send(ws, "call.start")
        send(ws, "webrtc.offer", {"sdp": "sdp"})
        until(ws, "webrtc.answer")
        backend = FakeBackend.instances[-1]
        call = backend.events
        assert isinstance(call, Call)
        session = daemon.sessions[str(repo.resolve())]
        now = call._clock()  # pyright: ignore[reportPrivateUsage]
        start = now - 2500
        pick = make_pick(1).model_copy(update={"created_at": start + 450})

        def act() -> None:
            session.add_pick(pick)
            ws_ = words("make this red", start)
            call.user_speaking()
            call.user_stopped()
            call.user_segment("make this red", ws_, start)
            call.user_turn_ended("make this red")

        assert client.portal is not None
        client.portal.call(_run_sync, act)
        user, _ = until(ws, "transcript.final", _role("user"))
        assert user["payload"]["text"] == "make [1] this red"
        assert user["payload"]["pickIds"] == [1]
        client.portal.call(backend.play)
        # The talker dispatched with the pick and the page it was made on.
        created, _ = until(ws, "task.created")
        assert created["payload"]["pickIds"] == [1]
        assert created["payload"]["page"]["route"] == "/cards"  # the voice turn's page
        talker = FakeTalker.instances[-1]
        assert "Picks:\n[1] div.card-header" in talker.prompts[-1]
        agent, _ = until(ws, "transcript.final", _role("agent"))
        assert agent["payload"]["text"] == "On it. Queued."
    finally:
        ws.__exit__(None, None, None)


async def _run_sync(fn: Callable[[], None]) -> None:
    fn()


def test_task_result_during_call_is_spoken_not_notified(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    ws = bound(client, config, repo)
    try:
        session = daemon.sessions[str(repo.resolve())]
        # Without a call: a notification whose click starts one.
        assert client.portal is not None
        client.portal.call(_dispatch, session, "Red heading")
        note, seen = until(ws, "notify")
        assert note["payload"]["action"] == "call"
        assert session.has_announcements()
        send(ws, "call.start")
        send(ws, "webrtc.offer", {"sdp": "sdp"})
        until(ws, "webrtc.answer")
        backend = FakeBackend.instances[-1]
        call = backend.events
        assert isinstance(call, Call)
        # The call opens with the summary.
        call._connected_at = 0  # pyright: ignore[reportPrivateUsage]
        client.portal.call(_tick, call)
        assert len(backend.replies) == 1
        client.portal.call(backend.play)
        agent, _ = until(ws, "transcript.final", _role("agent"))
        assert agent["payload"]["text"] == "Update."
        talker = FakeTalker.instances[-1]
        assert "just started a voice call" in talker.prompts[-1]
        assert "Task 'Red heading' completed" in talker.prompts[-1]
        assert not session.has_announcements()
        # During the call: no notification, an announcement instead.
        client.portal.call(_dispatch, session, "Card spacing")
        _done, seen = until(ws, "transcript.final", lambda m: "completed" in m["payload"]["text"])
        assert session.has_announcements()
        assert "notify" not in [m["type"] for m in seen]
        call._last_user_activity = None  # pyright: ignore[reportPrivateUsage]
        client.portal.call(_tick, call)
        assert len(backend.replies) == 2
        client.portal.call(backend.play)
        assert "has paused" in talker.prompts[-1]
        assert "Task 'Card spacing' completed" in talker.prompts[-1]
    finally:
        ws.__exit__(None, None, None)


async def _dispatch(session: Any, title: str) -> None:
    session._enqueue(make_brief(title), None)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not session.queue.active:
            return


async def _tick(call: Call) -> None:
    call._tick()  # pyright: ignore[reportPrivateUsage]
    await asyncio.sleep(0.05)


def test_call_needs_repo_and_start(client: TestClient, config: Config) -> None:
    with client.websocket_connect("/ws", headers={"origin": daemon_origin(config)}) as ws:
        hello(ws, config.token, None)
        assert ws.receive_json()["payload"]["code"] == "no_origin"
        send(ws, "call.start")
        assert ws.receive_json()["payload"]["code"] == "no_repo"
        send(ws, "webrtc.offer", {"sdp": "x"})
        assert ws.receive_json()["payload"]["code"] == "no_call"


def daemon_origin(config: Config) -> str:
    return f"chrome-extension://{config.extension_id}"


def test_socket_close_tears_down_call(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    send(ws, "call.start")
    send(ws, "webrtc.offer", {"sdp": "sdp"})
    until(ws, "webrtc.answer")
    ws.__exit__(None, None, None)
    backend = FakeBackend.instances[-1]
    for _ in range(50):
        if backend.stopped:
            break
        client.portal.call(asyncio.sleep, 0.01)  # pyright: ignore[reportOptionalMemberAccess]
    assert backend.stopped == 1


def test_backend_failure_reports_error_and_idles(
    config: Config, repo: Path, tmp_path: Path
) -> None:
    def failing(events: CallEvents) -> FakeBackend:
        return FakeBackend(events, fail=True)

    daemon = Daemon(
        config,
        talker_factory=FakeTalker,
        worker_factory=FakeWorker,
        ping_interval=3600,
        call_backend_factory=failing,
    )
    with TestClient(create_app(daemon)) as client:
        ws = bound(client, config, repo)
        try:
            send(ws, "call.start")
            send(ws, "webrtc.offer", {"sdp": "sdp"})
            err, seen = until(ws, "error")
            assert err["payload"]["code"] == "call_failed"
            states_seen: list[Any] = [
                m["payload"]["state"] for m in seen if m["type"] == "call.state"
            ]
            if "idle" not in states_seen:
                until(ws, "call.state", _state("idle"))
            send(ws, "call.start")
            assert until(ws, "call.state")[0]["payload"]["state"] == "connecting"
        finally:
            ws.__exit__(None, None, None)


def test_rebinding_to_another_repo_ends_call(
    client: TestClient, config: Config, repo: Path, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    config.map_repo(APP2, other)
    ws = bound(client, config, repo, APP)
    try:
        send(ws, "call.start")
        send(ws, "webrtc.offer", {"sdp": "sdp"})
        until(ws, "webrtc.answer")
        hello(ws, config.token, APP2)
        msgs: list[Msg] = []
        _w, seen = until(ws, "welcome")
        msgs += seen
        if not any(m["type"] == "call.state" and m["payload"]["state"] == "idle" for m in msgs):
            until(ws, "call.state", _state("idle"))
        assert FakeBackend.instances[-1].stopped == 1
    finally:
        ws.__exit__(None, None, None)
