"""Voice call signalling and state machine, with a fake media backend (no models)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient
from test_server import APP, APP2, FakeTalker, FakeWorker, Msg, bound, hello, send, until

from sidecall.config import Config
from sidecall.protocol.messages import ServerMessage
from sidecall.protocol.models import Role
from sidecall.server import Daemon, create_app
from sidecall.voice.call import Call, CallEvents, TalkerFn, echo_talker


class FakeBackend:
    instances: list[FakeBackend] = []  # noqa: RUF012

    def __init__(self, events: CallEvents, talker: TalkerFn, *, fail: bool = False) -> None:
        self.events = events
        self.talker = talker
        self.fail = fail
        self.offers: list[str] = []
        self.muted: list[bool] = []
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

    async def stop(self) -> None:
        self.stopped += 1

    async def say(self, text: str) -> None:
        """Simulate one full voice turn through the pipeline's events."""
        ev = self.events
        ev.user_speaking()
        ev.user_segment(text)
        ev.user_turn(text)
        async for chunk in self.talker(text):
            ev.agent_delta(chunk)
        ev.agent_done(False)
        ev.bot_speaking(True)
        ev.latency(512.0)
        ev.bot_speaking(False)


# --- Call state machine (pure) -------------------------------------------------------


class FakeSession:
    def __init__(self) -> None:
        self.emitted: list[ServerMessage] = []
        self.entries: list[tuple[Role, str, str]] = []
        self._n = 0

    def emit(self, msg: ServerMessage) -> None:
        self.emitted.append(msg)

    def new_turn_id(self) -> str:
        self._n += 1
        return f"turn{self._n}"

    def add_transcript(self, role: Role, text: str, *, turn_id: str | None = None) -> str:
        turn_id = turn_id or self.new_turn_id()
        self.entries.append((role, text, turn_id))
        return turn_id


def make_call(talker: TalkerFn = echo_talker) -> tuple[Call, FakeSession, list[ServerMessage]]:
    sent: list[ServerMessage] = []
    session = FakeSession()
    call = Call(session, sent.append, FakeBackend, talker)
    return call, session, sent


def states(sent: list[ServerMessage]) -> list[str]:
    return [m.payload.state for m in sent if m.type == "call.state"]


async def test_echo_talker_streams_words() -> None:
    chunks = [c async for c in echo_talker(" make it red ")]
    assert "".join(chunks) == "You said: make it red."
    assert len(chunks) == 5
    assert "".join([c async for c in echo_talker("Why?")]) == "You said: Why?"


async def test_call_happy_path_states_and_transcript() -> None:
    call, session, sent = make_call()
    backend = call.backend
    assert isinstance(backend, FakeBackend)
    assert await call.offer("v=0 offer") == "answer for v=0 offer"
    await backend.say("make it red")
    assert states(sent) == ["connecting", "listening", "thinking", "speaking", "listening"]
    partials = [m.payload.text for m in sent if m.type == "transcript.partial"]
    assert partials == ["…", "make it red"]
    assert [(r, t) for r, t, _ in session.entries] == [
        ("user", "make it red"),
        ("agent", "You said: make it red."),
    ]
    agent_turn = session.entries[1][2]
    deltas = [m for m in session.emitted if m.type == "agent.text.delta"]
    assert deltas and all(m.payload.turn_id == agent_turn for m in deltas)
    assert session.emitted[-1].type == "agent.text.done"
    assert call.latencies == [512.0]
    await call.end()
    assert states(sent)[-1] == "idle"
    assert backend.stopped == 1
    await call.end()  # idempotent
    assert backend.stopped == 1


async def test_interruption_records_partial_reply_and_listens() -> None:
    call, session, sent = make_call()
    await call.offer("sdp")
    call.user_turn("tell me a story")
    call.agent_delta("Once upon ")
    call.bot_speaking(True)
    call.agent_done(True)  # TalkerProcessor cancelled the reply
    call.interrupted()
    assert states(sent)[-2:] == ["speaking", "listening"]
    assert session.entries[-1][:2] == ("agent", "Once upon …")
    # A second interruption doesn't record anything twice.
    call.interrupted()
    assert len(session.entries) == 2


async def test_empty_reply_returns_to_listening() -> None:
    async def silent(text: str):
        if False:
            yield text

    call, _session, sent = make_call(silent)
    await call.offer("sdp")
    call.user_turn("hi")
    call.agent_done(False)
    assert states(sent)[-2:] == ["thinking", "listening"]


async def test_end_flushes_open_reply_and_ignores_late_events() -> None:
    call, session, sent = make_call()
    await call.offer("sdp")
    call.user_turn("hello")
    call.agent_delta("You said")
    await call.end()
    assert session.entries[-1][:2] == ("agent", "You said …")
    n = len(sent)
    call.bot_speaking(True)
    call.user_turn("late")
    assert len(sent) == n
    assert len(session.entries) == 2


async def test_second_offer_and_offer_after_end_are_errors() -> None:
    from sidecall.voice.call import CallError

    call, _s, _sent = make_call()
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
    call = Call(FakeSession(), sent.append, FakeBackend, echo_talker, on_closed=closed.append)
    await call.offer("sdp")
    call.closed()
    await asyncio.sleep(0)
    assert call.ended and closed == [call]
    assert states(sent)[-1] == "idle"


def test_mute_reaches_backend() -> None:
    call, _s, _sent = make_call()
    call.set_muted(True)
    call.set_muted(False)
    assert isinstance(call.backend, FakeBackend)
    assert call.backend.muted == [True, False]


# --- signalling over the WebSocket ---------------------------------------------------


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


def test_call_signalling_and_voice_turn(
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

        # Drive a voice turn from the pipeline side (on the daemon's loop).
        assert client.portal is not None
        client.portal.call(backend.say, "make it red")
        user, seen = until(ws, "transcript.final")
        assert user["payload"]["role"] == "user"
        assert user["payload"]["text"] == "make it red"
        agent, seen2 = until(ws, "transcript.final")
        assert agent["payload"]["text"] == "You said: make it red."
        types = [m["type"] for m in seen + seen2]
        assert "agent.text.delta" in types and "agent.text.done" in types
        assert backend.muted == [True]

        session = daemon.sessions[str(repo.resolve())]
        assert [(e.role, e.text) for e in session.transcript.recent()] == [
            ("user", "make it red"),
            ("agent", "You said: make it red."),
        ]

        send(ws, "call.end")
        until(ws, "call.state", _state("idle"))
        assert backend.stopped == 1
    finally:
        ws.__exit__(None, None, None)


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
    def failing(events: CallEvents, talker: TalkerFn) -> FakeBackend:
        return FakeBackend(events, talker, fail=True)

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
            # A new call can start afterwards.
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
