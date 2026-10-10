from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from helpers import make_brief, make_pick
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sidecall.config import Config
from sidecall.protocol.models import Pick, Target, Task
from sidecall.server import Daemon, PairingCode, create_app, normalise_origin
from sidecall.session import OnDelta, OnEvent, RepoSession, WorkerResult
from sidecall.talker import MCP_TOOL_NAMES, build_tools
from sidecall.uploads import MAX_UPLOAD_BYTES, Image, LabelledImage

EXT_ORIGIN = "chrome-extension://kcjkimilmanlonlplfancfpoekkphjhb"
APP = "http://localhost:5173"
APP2 = "http://127.0.0.1:3000"

Msg = dict[str, Any]


# --- fakes ---------------------------------------------------------------------------


class FakeTalker:
    """Dispatches a task titled with the user's text. 'hang' waits for an interrupt;
    'chat' just replies."""

    instances: list[FakeTalker] = []  # noqa: RUF012

    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self.prompts: list[str] = []
        self.images: list[Image | None] = []
        self.extras: list[Sequence[LabelledImage]] = []
        self.interrupts = 0
        self._interrupted = asyncio.Event()
        FakeTalker.instances.append(self)

    async def reply(
        self,
        prompt: str,
        on_delta: OnDelta,
        image: Image | None = None,
        extra_images: Sequence[LabelledImage] = (),
    ) -> str:
        self.prompts.append(prompt)
        self.images.append(image)
        self.extras.append(extra_images)
        text = next(ln for ln in prompt.splitlines() if ln.startswith("User: "))[len("User: ") :]
        if text == "hang":
            on_delta("Thinking")
            await self._interrupted.wait()
            self._interrupted.clear()
            return "Thinking"
        if text == "chat":
            on_delta("Hello ")
            on_delta("there.")
            return "Hello there."
        on_delta("On it. ")
        self.session.dispatch(make_brief(text))
        on_delta("Queued.")
        return "On it. Queued."

    async def interrupt(self) -> None:
        self.interrupts += 1
        self._interrupted.set()

    async def close(self) -> None:
        pass


class FakeWorker:
    """Behaviour by task title: 'approve' asks for approval, 'slow' runs until
    interrupted, 'fail' fails, anything else edits a file and completes."""

    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self._stop = asyncio.Event()

    async def run(self, task: Task, on_event: OnEvent) -> WorkerResult:
        if task.title == "approve":
            ok = await self.session.approvals.request(
                task.id, "pnpm add zod", "package install: pnpm add zod"
            )
            return WorkerResult("completed", summary=f"approved={ok}")
        if task.title.startswith("slow"):
            on_event("tool", "Working slowly")
            await self._stop.wait()
            self._stop.clear()
            return WorkerResult("cancelled")
        if task.title == "fail":
            return WorkerResult("failed", error="it broke")
        on_event("edit", "Editing src/routes/+page.svelte")
        return WorkerResult("completed", summary="Made it red.", files=["src/routes/+page.svelte"])

    async def interrupt(self) -> None:
        self._stop.set()

    async def close(self) -> None:
        pass


# --- fixtures ------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "app"
    path.mkdir()
    return path


@pytest.fixture
def repo2(tmp_path: Path) -> Path:
    path = tmp_path / "app2"
    path.mkdir()
    return path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.load(tmp_path / "home")


def make_daemon(config: Config, browse_result: str | None = None, ping: float = 3600) -> Daemon:
    async def browse(origin: str) -> str | None:
        return browse_result

    return Daemon(
        config,
        talker_factory=FakeTalker,
        worker_factory=FakeWorker,
        browse=browse,
        ping_interval=ping,
    )


@pytest.fixture
def daemon(config: Config) -> Daemon:
    return make_daemon(config)


@pytest.fixture
def client(daemon: Daemon) -> Iterator[TestClient]:
    with TestClient(create_app(daemon)) as c:
        yield c


def connect(client: TestClient, origin: str = EXT_ORIGIN) -> Any:
    return client.websocket_connect("/ws", headers={"origin": origin})


def send(ws: Any, mtype: str, payload: Msg | None = None) -> None:
    ws.send_json({"type": mtype, "id": "c1", "ts": 0, "payload": payload or {}})


def hello(ws: Any, token: str, origin: str | None = APP) -> None:
    send(ws, "hello", {"token": token, "extVersion": "0.1.0", "origin": origin, "clockSample": 0})


def until(ws: Any, mtype: str, pred: Callable[[Msg], bool] | None = None) -> tuple[Msg, list[Msg]]:
    """Receive until a message of `mtype` (matching `pred`); return it and all seen."""
    seen: list[Msg] = []
    for _ in range(100):
        msg: Msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == mtype and (pred is None or pred(msg)):
            return msg, seen
    raise AssertionError(f"no {mtype} in {[m['type'] for m in seen]}")


def bound(client: TestClient, config: Config, repo: Path, origin: str = APP) -> Any:
    config.map_repo(origin, repo)
    ws = connect(client)
    ws.__enter__()
    hello(ws, config.token, origin)
    msg = ws.receive_json()
    assert msg["type"] == "welcome", msg
    return ws


# --- pure helpers --------------------------------------------------------------------


def test_normalise_origin() -> None:
    assert normalise_origin("http://localhost:5173/") == "http://localhost:5173"
    assert normalise_origin("http://127.0.0.1:3000") == "http://127.0.0.1:3000"
    assert normalise_origin("http://localhost") == "http://localhost"
    assert normalise_origin("https://localhost:5173") is None
    assert normalise_origin("http://example.com") is None
    assert normalise_origin("http://localhost.evil.com") is None
    assert normalise_origin(None) is None


def test_pairing_code_expiry_and_single_use() -> None:
    now = [0.0]
    p = PairingCode(ttl=600, clock=lambda: now[0])
    code = p.issue()
    assert re.fullmatch(r"\d{6}", code)
    assert not p.consume("000000" if code != "000000" else "111111")
    assert p.consume(code)
    assert not p.consume(code)  # used
    code = p.issue()
    now[0] = 601
    assert not p.consume(code)  # expired


def test_pairing_code_locks_after_many_failures() -> None:
    p = PairingCode()
    code = p.issue()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(10):
        p.consume(wrong)
    assert not p.consume(code)


# --- HTTP + connection ---------------------------------------------------------------


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"ok": True}


@pytest.mark.parametrize("origin", ["http://localhost:5173", "chrome-extension://otherid", ""])
def test_bad_origin_is_rejected(client: TestClient, origin: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc, connect(client, origin):
        pass
    assert exc.value.code == 4403


def test_pairing_over_websocket(client: TestClient, daemon: Daemon, config: Config) -> None:
    code = daemon.pairing.issue()
    with connect(client) as ws:
        send(ws, "pair.request", {"code": "999999" if code != "999999" else "888888"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["payload"]["code"] == "bad_pairing_code"
        send(ws, "pair.request", {"code": code})
        msg = ws.receive_json()
        assert msg == {**msg, "type": "pair.result", "payload": {"token": config.token}}
        send(ws, "pair.request", {"code": code})
        assert ws.receive_json()["payload"]["code"] == "bad_pairing_code"


def test_bad_token_closes_4401(client: TestClient) -> None:
    with connect(client) as ws:
        hello(ws, "wrong-token")
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["payload"]["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_message_before_hello_closes_4401(client: TestClient) -> None:
    with connect(client) as ws:
        send(ws, "user.text", {"text": "hi", "pickIds": []})
        assert ws.receive_json()["payload"]["code"] == "unauthorized"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_bad_message(client: TestClient, config: Config) -> None:
    with connect(client) as ws:
        ws.send_text("{nope")
        assert ws.receive_json()["payload"]["code"] == "bad_message"
        send(ws, "made.up")
        assert ws.receive_json()["payload"]["code"] == "bad_message"


def test_no_origin_keeps_connection_authenticated(
    client: TestClient, config: Config, repo: Path
) -> None:
    with connect(client) as ws:
        hello(ws, config.token, None)
        assert ws.receive_json()["payload"]["code"] == "no_origin"
        hello(ws, config.token, "https://example.com")
        assert ws.receive_json()["payload"]["code"] == "no_origin"
        send(ws, "user.text", {"text": "hi", "pickIds": []})
        assert ws.receive_json()["payload"]["code"] == "no_repo"
        send(ws, "repo.map", {"origin": APP, "path": str(repo)})
        assert ws.receive_json()["type"] == "welcome"


def test_unknown_origin_then_map(client: TestClient, config: Config, repo: Path) -> None:
    with connect(client) as ws:
        hello(ws, config.token, APP)
        msg = ws.receive_json()
        assert msg["type"] == "repo.unknown" and msg["payload"] == {"origin": APP}

        send(ws, "repo.map", {"origin": APP, "path": str(repo / "missing")})
        assert ws.receive_json()["payload"]["code"] == "bad_path"
        send(ws, "repo.map", {"origin": APP, "path": "relative/path"})
        assert ws.receive_json()["payload"]["code"] == "bad_path"
        send(ws, "repo.map", {"origin": "http://example.com", "path": str(repo)})
        assert ws.receive_json()["payload"]["code"] == "bad_origin"

        send(ws, "repo.map", {"origin": APP, "path": str(repo)})
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome"
        p = welcome["payload"]
        assert p["repo"] == {"name": "app", "path": str(repo.resolve()), "origin": APP}
        assert p["state"] == {
            "tasks": [],
            "approvals": [],
            "transcript": [],
            "settings": {"preset": "default"},
        }
        assert isinstance(p["clockOffset"], float) and p["clockOffset"] > 0
    assert Config.load(config.home).repos == {APP: str(repo.resolve())}

    with connect(client) as ws:
        hello(ws, config.token, APP)
        assert ws.receive_json()["type"] == "welcome"


def test_repo_browse(config: Config, repo: Path) -> None:
    daemon = make_daemon(config, browse_result=str(repo))
    with TestClient(create_app(daemon)) as c, connect(c) as ws:
        hello(ws, config.token, APP)
        assert ws.receive_json()["type"] == "repo.unknown"
        send(ws, "repo.browse", {"origin": APP})
        assert ws.receive_json()["type"] == "welcome"
    with TestClient(create_app(make_daemon(config, browse_result=None))) as c, connect(c) as ws:
        hello(ws, config.token, APP2)
        assert ws.receive_json()["type"] == "repo.unknown"
        send(ws, "repo.browse", {"origin": APP2})
        assert ws.receive_json()["payload"]["code"] == "browse_cancelled"


def test_rehello_rebinds(
    client: TestClient, daemon: Daemon, config: Config, repo: Path, repo2: Path
) -> None:
    config.map_repo(APP, repo)
    config.map_repo(APP2, repo2)
    with connect(client) as ws:
        hello(ws, config.token, APP)
        assert ws.receive_json()["payload"]["repo"]["name"] == "app"
        hello(ws, config.token, APP2)
        assert ws.receive_json()["payload"]["repo"]["name"] == "app2"
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
    s1 = daemon.sessions[str(repo.resolve())]
    s2 = daemon.sessions[str(repo2.resolve())]
    assert s1.transcript.recent() == []
    assert [e.role for e in s2.transcript.recent()] == ["user", "agent"]


def test_webrtc_ice_is_ignored(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "webrtc.ice", {"candidate": None})  # non-trickle: no reply
        send(ws, "call.end")  # no call: no-op, no reply
        send(ws, "call.mute", {"muted": True})
        assert ws.receive_json()["payload"]["code"] == "no_call"
    finally:
        ws.__exit__(None, None, None)


def test_ping(config: Config) -> None:
    with TestClient(create_app(make_daemon(config, ping=0.05))) as c, connect(c) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "ping" and msg["payload"] == {"seq": 1}
        assert ws.receive_json()["payload"] == {"seq": 2}


# --- turns and tasks -----------------------------------------------------------------


def test_text_turn_dispatch_and_completion(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "user.text", {"text": "make it red", "pickIds": []})
        done, seen = until(ws, "task.completed")
        types = [m["type"] for m in seen]
        assert types[0] == "transcript.final" and seen[0]["payload"]["role"] == "user"
        assert seen[0]["payload"]["text"] == "make it red"
        # dispatch ordering: task.created, queue.state, system transcript
        i = types.index("task.created")
        assert types[i : i + 3] == ["task.created", "queue.state", "transcript.final"]
        task_id = seen[i]["payload"]["id"]
        assert seen[i + 2]["payload"] == {
            **seen[i + 2]["payload"],
            "role": "system",
            "taskId": task_id,
            "text": "Task queued: make it red",
        }
        deltas = [m["payload"]["delta"] for m in seen if m["type"] == "agent.text.delta"]
        assert "".join(deltas) == "On it. Queued."
        assert done["payload"] == {
            "taskId": task_id,
            "summary": "Made it red.",
            "files": ["src/routes/+page.svelte"],
        }
        events = [m["payload"]["text"] for m in seen if m["type"] == "task.event"]
        assert events == ["Started", "Editing src/routes/+page.svelte"]
        # results: completed -> queue.state -> system transcript -> notify
        _, rest = until(ws, "notify")
        assert [m["type"] for m in rest] == ["queue.state", "transcript.final", "notify"]
        assert rest[0]["payload"]["tasks"][0]["status"] == "completed"

        # the talker hears about the result on its next turn
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        talker = FakeTalker.instances[-1]
        assert talker.prompts[-1].startswith("[System] Task 'make it red' completed: Made it red.")
        assert talker.prompts[-1].endswith("User: chat")
    finally:
        ws.__exit__(None, None, None)

    with connect(client) as ws2:
        hello(ws2, config.token, APP)
        state = ws2.receive_json()["payload"]["state"]
        assert [t["status"] for t in state["tasks"]] == ["completed"]
        roles = [e["role"] for e in state["transcript"]]
        assert roles[:4] == ["user", "system", "agent", "system"]


def test_agent_turn_events_order(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        final, seen = until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        types = [m["type"] for m in seen]
        assert types == [
            "transcript.final",
            "agent.text.delta",
            "agent.text.delta",
            "agent.text.done",
            "transcript.final",
        ]
        turn_id = seen[1]["payload"]["turnId"]
        assert seen[3]["payload"]["turnId"] == turn_id
        assert final["payload"]["turnId"] == turn_id
        assert final["payload"]["text"] == "Hello there."
    finally:
        ws.__exit__(None, None, None)


def test_new_text_interrupts_talker(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "user.text", {"text": "hang", "pickIds": []})
        until(ws, "agent.text.delta")
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        until(ws, "transcript.final", lambda m: m["payload"]["text"] == "Hello there.")
        assert FakeTalker.instances[-1].interrupts == 1
    finally:
        ws.__exit__(None, None, None)


@pytest.mark.parametrize("allow", [True, False])
def test_approval_flow(client: TestClient, config: Config, repo: Path, allow: bool) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "user.text", {"text": "approve", "pickIds": []})
        blocked, seen = until(ws, "task.blocked")
        approval = blocked["payload"]["approval"]
        assert approval["command"] == "pnpm add zod"
        assert approval["reason"] == "package install: pnpm add zod"
        statuses = [
            m["payload"]["tasks"][0]["status"]
            for m in seen
            if m["type"] == "queue.state" and m["payload"]["tasks"]
        ]
        assert statuses[-1] == "blocked"
        assert ws.receive_json()["type"] == "notify"

        # a second client sees the pending approval in welcome.state
        with connect(client) as ws2:
            hello(ws2, config.token, APP)
            state = ws2.receive_json()["payload"]["state"]
            assert [a["id"] for a in state["approvals"]] == [approval["id"]]

        send(ws, "approval.respond", {"id": "nope", "allow": True})
        assert until(ws, "error")[0]["payload"]["code"] == "unknown_approval"
        send(ws, "approval.respond", {"id": approval["id"], "allow": allow})
        resolved, seen = until(ws, "approval.resolved")
        assert resolved["payload"] == {"id": approval["id"], "allow": allow}
        done, _ = until(ws, "task.completed")
        assert done["payload"]["summary"] == f"approved={allow}"
    finally:
        ws.__exit__(None, None, None)


def test_cancel_running_and_queued_and_reorder(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    ws = bound(client, config, repo)
    try:
        for text in ["slow", "b", "c", "d"]:
            send(ws, "user.text", {"text": text, "pickIds": []})
            until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        session = daemon.sessions[str(repo.resolve())]
        ids = {t.title: t.id for t in session.queue.all_tasks()}

        send(ws, "task.reorder", {"ids": [ids["d"], "unknown", ids["slow"], ids["b"]]})
        msg, _ = until(ws, "queue.state")
        assert [t["title"] for t in msg["payload"]["tasks"]] == ["slow", "d", "b", "c"]

        send(ws, "task.cancel", {"id": ids["b"]})
        msg, _ = until(ws, "queue.state")
        assert [(t["title"], t["status"]) for t in msg["payload"]["tasks"]] == [
            ("slow", "running"),
            ("d", "queued"),
            ("c", "queued"),
            ("b", "cancelled"),
        ]

        send(ws, "task.cancel", {"id": "missing"})
        assert until(ws, "error")[0]["payload"]["code"] == "unknown_task"

        send(ws, "task.cancel", {"id": ids["slow"]})
        until(ws, "transcript.final", lambda m: m["payload"]["text"] == "Task cancelled: slow")
        # the queue moves on to the next task
        until(ws, "task.completed", lambda m: m["payload"]["taskId"] == ids["d"])
        until(ws, "task.completed", lambda m: m["payload"]["taskId"] == ids["c"])
        statuses = {t.title: t.status for t in session.queue.all_tasks()}
        assert statuses == {
            "slow": "cancelled",
            "b": "cancelled",
            "d": "completed",
            "c": "completed",
        }
    finally:
        ws.__exit__(None, None, None)


def test_failed_task(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "user.text", {"text": "fail", "pickIds": []})
        failed, _ = until(ws, "task.failed")
        assert failed["payload"]["reason"] == "it broke"
        _, rest = until(ws, "notify")
        assert [m["type"] for m in rest] == ["queue.state", "transcript.final", "notify"]
    finally:
        ws.__exit__(None, None, None)


def test_settings_update_persists(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "settings.update", {"preset": "cautious"})
    finally:
        ws.__exit__(None, None, None)
    with connect(client) as ws2:
        hello(ws2, config.token, APP)
        assert ws2.receive_json()["payload"]["state"]["settings"] == {"preset": "cautious"}


def test_queue_survives_restart(config: Config, repo: Path) -> None:
    config.map_repo(APP, repo)
    with TestClient(create_app(make_daemon(config))) as c, connect(c) as ws:
        hello(ws, config.token, APP)
        ws.receive_json()
        seen: list[Msg] = []
        for text in ["slow 1", "slow 2"]:
            send(ws, "user.text", {"text": text, "pickIds": []})
            seen += until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")[1]
        if not any(m["payload"].get("text") == "Working slowly" for m in seen):
            until(ws, "task.event", lambda m: m["payload"]["text"] == "Working slowly")

    # the daemon stopped with "slow 1" running; a new daemon requeues it at the front
    with TestClient(create_app(make_daemon(config))) as c, connect(c) as ws:
        hello(ws, config.token, APP)
        tasks = ws.receive_json()["payload"]["state"]["tasks"]
        assert [t["title"] for t in tasks] == ["slow 1", "slow 2"]
        assert tasks[0]["status"] in ("queued", "running")


# --- uploads and page context --------------------------------------------------------

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def upload(
    client: TestClient,
    token: str,
    body: bytes = JPEG,
    content_type: str = "image/jpeg",
    origin: str = EXT_ORIGIN,
) -> Any:
    headers = {"authorization": f"Bearer {token}", "origin": origin, "content-type": content_type}
    return client.post("/api/upload", content=body, headers=headers)


def test_upload_success(client: TestClient, config: Config) -> None:
    res = upload(client, config.token)
    assert res.status_code == 200
    ref = res.json()["ref"]
    assert re.fullmatch(r"[0-9a-f]{32}", ref)
    assert (config.home / "uploads" / f"{ref}.jpg").read_bytes() == JPEG
    res = upload(client, config.token, b"png-bytes", "image/png")
    assert (config.home / "uploads" / f"{res.json()['ref']}.png").read_bytes() == b"png-bytes"


def test_upload_rejections(client: TestClient, config: Config) -> None:
    assert upload(client, "wrong").status_code == 401
    assert upload(client, "").status_code == 401
    no_scheme = client.post(
        "/api/upload",
        content=JPEG,
        headers={"authorization": config.token, "origin": EXT_ORIGIN, "content-type": "image/jpeg"},
    )
    assert no_scheme.status_code == 401
    assert upload(client, config.token, origin="http://localhost:5173").status_code == 403
    assert upload(client, config.token, origin="chrome-extension://other").status_code == 403
    assert upload(client, config.token, content_type="image/gif").status_code == 415
    assert upload(client, config.token, content_type="text/plain").status_code == 415
    big = b"\0" * (MAX_UPLOAD_BYTES + 1)
    assert upload(client, config.token, big).status_code == 413
    assert upload(client, config.token, b"").status_code == 400
    assert client.get("/api/upload").status_code == 405
    assert not (config.home / "uploads").exists() or not any((config.home / "uploads").iterdir())


def test_old_uploads_are_pruned_at_startup(config: Config) -> None:
    uploads = config.home / "uploads"
    uploads.mkdir(parents=True)
    old = uploads / ("a" * 32 + ".jpg")
    old.write_bytes(b"x")
    fresh = uploads / ("b" * 32 + ".jpg")
    fresh.write_bytes(b"y")
    stamp = old.stat().st_mtime - 8 * 24 * 3600
    os.utime(old, (stamp, stamp))
    with TestClient(create_app(make_daemon(config))):
        pass
    assert not old.exists() and fresh.exists()


def test_turn_with_page_reaches_talker_and_task(
    client: TestClient, config: Config, repo: Path
) -> None:
    ref = upload(client, config.token).json()["ref"]
    page = {
        "url": f"{APP}/settings?tab=a#x",
        "route": "/settings?tab=a#x",
        "title": "Settings",
        "viewport": {"w": 1440, "h": 900, "dpr": 2},
        "screenshot": ref,
    }
    ws = bound(client, config, repo)
    try:
        send(ws, "user.text", {"text": "magenta dropdown", "pickIds": [], "page": page})
        created, _ = until(ws, "task.created")
        assert created["payload"]["page"] == page
        until(ws, "task.completed")
        talker = FakeTalker.instances[-1]
        prompt = talker.prompts[-1]
        assert f'Page: "Settings" — {APP}/settings?tab=a#x' in prompt
        assert "(route /settings?tab=a#x, viewport 1440x900)" in prompt
        assert "A screenshot of what the user currently sees is attached." in prompt
        assert talker.images[-1] == Image(JPEG, "image/jpeg")

        # A missing upload: the turn still runs, without the image or the claim.
        gone = {**page, "screenshot": "f" * 32}
        send(ws, "user.text", {"text": "chat", "pickIds": [], "page": gone})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        assert talker.images[-1] is None
        assert "screenshot" not in talker.prompts[-1]

        # No page at all: text only; tasks it dispatches have no page.
        send(ws, "user.text", {"text": "plain", "pickIds": []})
        created, _ = until(ws, "task.created")
        assert created["payload"]["page"] is None
        assert "Page:" not in talker.prompts[-1]
    finally:
        ws.__exit__(None, None, None)


# --- picks ---------------------------------------------------------------------------


def wire(pick: Pick) -> Msg:
    return pick.model_dump(mode="json", by_alias=True)


def session_of(daemon: Daemon) -> RepoSession:
    return next(iter(daemon.sessions.values()))


def seed_repo(repo: Path) -> None:
    (repo / "src/lib").mkdir(parents=True)
    (repo / "src/lib/Card.svelte").write_text("<div class='card-header'></div>\n")


def test_pick_add_remove_clear(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    seed_repo(repo)
    ws = bound(client, config, repo)
    try:
        send(ws, "pick.add", wire(make_pick(1, file="src/lib/Card.svelte", line=1, column=0)))
        send(ws, "pick.add", wire(make_pick(2, file="src/lib/Gone.svelte")))
        send(ws, "user.text", {"text": "chat", "pickIds": []})  # a barrier: handled in order
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        picks = session_of(daemon).picks
        one, two = picks.get(1), picks.get(2)
        assert one is not None and one.framework.source is not None
        assert one.framework.source.column == 1  # svelte: 0-based -> 1-based
        assert one.framework.component_chain[0].file == "src/lib/Card.svelte"
        assert two is not None and two.framework.source is None  # file doesn't exist

        send(ws, "pick.add", wire(make_pick(1, file="src/lib/Card.svelte", line=7, column=0)))
        send(ws, "pick.remove", {"id": 2})
        send(ws, "pick.remove", {"id": 99})  # unknown ids are ignored
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        assert 2 not in picks and len(picks) == 1
        updated = picks.get(1)
        assert updated is not None and updated.framework.source is not None
        assert updated.framework.source.line == 7  # newest wins

        send(ws, "picks.clear")
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        assert len(picks) == 0
    finally:
        ws.__exit__(None, None, None)


def test_bad_pick_add_is_a_bad_message(client: TestClient, config: Config, repo: Path) -> None:
    ws = bound(client, config, repo)
    try:
        send(ws, "pick.add", {"id": 1})
        assert ws.receive_json()["payload"]["code"] == "bad_message"
    finally:
        ws.__exit__(None, None, None)


def test_user_text_with_picks_reaches_the_talker(
    client: TestClient, daemon: Daemon, config: Config, repo: Path
) -> None:
    seed_repo(repo)
    crop = upload(client, config.token).json()["ref"]
    viewport = upload(client, config.token).json()["ref"]
    ws = bound(client, config, repo)
    try:
        pick = make_pick(
            1, file="src/lib/Card.svelte", repeat=(12, 2), screenshots=(crop, viewport)
        )
        send(ws, "pick.add", wire(pick))
        send(ws, "pick.add", wire(make_pick(2, screenshots=("f" * 32, viewport))))  # crop is gone
        send(ws, "user.text", {"text": "chat", "pickIds": [1, 2, 9]})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        talker = FakeTalker.instances[-1]
        lines = talker.prompts[-1].splitlines()
        assert lines[0] == "User: chat"
        assert lines[1] == "Picks:"
        assert lines[2].startswith("[1] div.card-header — CardHeader — src/lib/Card.svelte:")
        assert "repeated 12×, instance 3" in lines[2]
        assert lines[3].startswith("[2] ")
        assert lines[4] == "[9] (no longer available)"
        assert [e.label for e in talker.extras[-1]] == ["[1] crop"]  # missing crops are skipped
        assert talker.extras[-1][0].image == Image(JPEG, "image/jpeg")
    finally:
        ws.__exit__(None, None, None)


def test_turn_images_are_capped_with_the_page_screenshot_first(
    client: TestClient, config: Config, repo: Path
) -> None:
    ref = upload(client, config.token).json()["ref"]
    page = {
        "url": f"{APP}/",
        "route": "/",
        "title": "T",
        "viewport": {"w": 1, "h": 1, "dpr": 1},
        "screenshot": ref,
    }
    ws = bound(client, config, repo)
    try:
        for i in range(1, 9):
            send(ws, "pick.add", wire(make_pick(i, screenshots=(ref, ref))))
        send(ws, "user.text", {"text": "chat", "pickIds": list(range(1, 9)), "page": page})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")
        talker = FakeTalker.instances[-1]
        assert talker.images[-1] is not None
        assert [e.label for e in talker.extras[-1]] == [f"[{i}] crop" for i in range(1, 6)]
    finally:
        ws.__exit__(None, None, None)


def test_task_snapshots_picks_and_survives_restart(config: Config, repo: Path) -> None:
    seed_repo(repo)
    config.map_repo(APP, repo)
    with TestClient(create_app(make_daemon(config))) as c, connect(c) as ws:
        hello(ws, config.token, APP)
        ws.receive_json()
        send(ws, "pick.add", wire(make_pick(1, file="src/lib/Card.svelte", repeat=(3, 0))))
        send(ws, "pick.add", wire(make_pick(2, file="src/lib/Card.svelte")))
        send(ws, "user.text", {"text": "slow 1", "pickIds": [1, 5]})
        created, _ = until(ws, "task.created")
        assert created["payload"]["pickIds"] == [1]  # only existing ids
        task_id = created["payload"]["id"]
        # later clears and id reuse can't change the queued task
        send(ws, "picks.clear")
        send(ws, "pick.add", wire(make_pick(1, file="src/lib/Card.svelte", line=99)))
        send(ws, "user.text", {"text": "chat", "pickIds": []})
        until(ws, "transcript.final", lambda m: m["payload"]["role"] == "agent")

    daemon2 = make_daemon(config)
    with TestClient(create_app(daemon2)) as c, connect(c) as ws:
        hello(ws, config.token, APP)
        ws.receive_json()
        session = session_of(daemon2)
        assert len(session.picks) == 0  # the store is per daemon run
        task = session.get_task(task_id)
        assert task is not None and task.pick_ids == [1]
        (pick,) = session.task_picks(task)
        assert pick.framework.source is not None and pick.framework.source.line == 14
        assert pick.repeat is not None and pick.repeat.count == 3


async def test_target_pick_ids_join_the_turns_picks(daemon: Daemon, repo: Path) -> None:
    seed_repo(repo)
    session = daemon.get_session(repo)
    session.add_pick(make_pick(1, file="src/lib/Card.svelte"))
    session.add_pick(make_pick(2, file="src/lib/Card.svelte"))
    session.add_pick(make_pick(3, file="src/lib/Card.svelte"))
    brief = make_brief("x")
    brief.targets = [Target(pick_id=3, description="a"), Target(pick_id=77, description="b")]
    session._turn_picks = [p for p in session.picks.resolve([1, 3]) if isinstance(p, Pick)]  # pyright: ignore[reportPrivateUsage]
    task = session.dispatch(brief)
    assert task.pick_ids == [3, 1]
    assert [p.id for p in session.task_picks(task)] == [3, 1]
    session._turn_picks = []  # pyright: ignore[reportPrivateUsage]
    assert session.dispatch(make_brief("y")).pick_ids == []
    assert session.request_undo().pick_ids == []


async def test_get_pick_tool(daemon: Daemon, repo: Path) -> None:
    seed_repo(repo)
    session = daemon.get_session(repo)
    session.add_pick(make_pick(1, file="src/lib/Card.svelte", repeat=(12, 2)))
    tools = {t.name: t for t in build_tools(session)}
    assert "get_pick" in tools
    assert "mcp__sidecall__get_pick" in MCP_TOOL_NAMES
    ok = await tools["get_pick"].handler({"id": 1})
    data = json.loads(ok["content"][0]["text"])
    assert data["id"] == 1
    assert data["framework"]["source"]["file"] == "src/lib/Card.svelte"
    assert data["framework"]["source"]["column"] == 4  # normalised: 3 + 1 (svelte)
    assert data["element"]["outerHTML"] and data["element"]["computedStyles"]
    assert data["repeat"] == {"count": 12, "index": 2}
    assert "is_error" not in ok
    missing = await tools["get_pick"].handler({"id": 2})
    assert missing["is_error"] and "No pick" in missing["content"][0]["text"]
    bad = await tools["get_pick"].handler({"id": "x"})
    assert bad["is_error"]
