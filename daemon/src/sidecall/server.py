"""HTTP + WebSocket server (SPEC §6.1, §9.1, §9.2)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
import subprocess
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .config import Config
from .protocol.messages import (
    CallEnd,
    CallMute,
    CallStart,
    ErrorMsg,
    ErrorPayload,
    Hello,
    PairRequest,
    PairResult,
    PairResultPayload,
    PickAdd,
    PickRemove,
    PicksClear,
    Ping,
    Pong,
    RepoBrowse,
    RepoMap,
    RepoUnknown,
    RepoUnknownPayload,
    SeqPayload,
    ServerMessage,
    SettingsUpdate,
    TaskCancel,
    TaskReorder,
    UserText,
    WebRTCAnswer,
    WebRTCAnswerPayload,
    WebRTCIceIn,
    WebRTCOffer,
    Welcome,
    WelcomePayload,
    parse_client,
)
from .session import RepoSession, TalkerFactory, WorkerFactory
from .uploads import MAX_UPLOAD_BYTES, UploadStore, media_type_of
from .voice.call import (
    BackendFactory,
    Call,
    CallBackend,
    CallError,
    CallEvents,
    TalkerFn,
    echo_talker,
)

log = logging.getLogger(__name__)

PING_INTERVAL = 20.0
PAIRING_TTL = 600.0
PAIRING_MAX_ATTEMPTS = 10
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403

_LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$")

BrowseFn = Callable[[str], Awaitable[str | None]]


def now_ms() -> float:
    return time.time() * 1000


def normalise_origin(origin: str | None) -> str | None:
    """A local dev-server origin (`http://localhost:*`, `http://127.0.0.1:*`), or None."""
    if not origin:
        return None
    origin = origin.strip().rstrip("/").lower()
    return origin if _LOCAL_ORIGIN.match(origin) else None


# --- pairing -----------------------------------------------------------------------


class PairingCode:
    """A 6-digit code valid for 10 minutes or until used (SPEC §9.1)."""

    def __init__(self, ttl: float = PAIRING_TTL, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._code: str | None = None
        self._expires = 0.0
        self._failures = 0

    def issue(self) -> str:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = self._clock() + self.ttl
        self._failures = 0
        return self._code

    @property
    def active(self) -> bool:
        return self._code is not None and self._clock() < self._expires

    def consume(self, code: str) -> bool:
        if not self.active or self._code is None:
            return False
        if secrets.compare_digest(code.strip(), self._code):
            self._code = None
            return True
        self._failures += 1
        if self._failures >= PAIRING_MAX_ATTEMPTS:
            self._code = None  # too many guesses: the user restarts to get a new code
        return False


# --- folder picker ---------------------------------------------------------------------


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _choose_folder_sync(origin: str) -> str | None:
    prompt = _applescript_string(f"Choose the repo folder for {origin}")
    script = f"POSIX path of (choose folder with prompt {prompt})"
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    path = proc.stdout.strip()
    return path or None


async def choose_folder(origin: str) -> str | None:
    """macOS folder picker. Returns a POSIX path, or None if cancelled."""
    return await asyncio.to_thread(_choose_folder_sync, origin)


# --- connections -------------------------------------------------------------------


@dataclass
class _Close:
    code: int


class Connection:
    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.authenticated = False
        self.clock_offset = 0.0
        self.origin: str | None = None
        self.session: RepoSession | None = None
        self.call: Call | None = None
        self.call_tasks: set[asyncio.Task[None]] = set()
        self._out: asyncio.Queue[str | _Close] = asyncio.Queue()
        self._seq = 0

    def send(self, msg: ServerMessage) -> None:
        self._out.put_nowait(msg.model_dump_json())

    def error(self, code: str, message: str) -> None:
        self.send(ErrorMsg(payload=ErrorPayload(code=code, message=message)))

    def close(self, code: int) -> None:
        self._out.put_nowait(_Close(code))

    async def writer(self) -> None:
        while True:
            item = await self._out.get()
            if isinstance(item, _Close):
                with contextlib.suppress(Exception):
                    await self.ws.close(code=item.code)
                return
            await self.ws.send_text(item)

    async def pinger(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            self._seq += 1
            self.send(Ping(payload=SeqPayload(seq=self._seq)))


# --- daemon ------------------------------------------------------------------------


def _default_talker_factory(session: RepoSession):  # pragma: no cover - needs Claude
    from .talker import ClaudeTalker

    return ClaudeTalker(session)


def _default_worker_factory(session: RepoSession):  # pragma: no cover - needs Claude
    from .worker import ClaudeWorker

    return ClaudeWorker(session)


def _default_backend_factory(events: CallEvents, talker: TalkerFn) -> CallBackend:
    from .voice.pipeline import PipecatBackend  # heavy: Pipecat, aiortc, MLX

    return PipecatBackend(events, talker)


def _default_voice_talker(session: RepoSession) -> TalkerFn:
    return echo_talker  # Phase 1 stub (M3); Phase 2 wires the real talker


class Daemon:
    def __init__(
        self,
        config: Config,
        *,
        talker_factory: TalkerFactory = _default_talker_factory,
        worker_factory: WorkerFactory = _default_worker_factory,
        browse: BrowseFn = choose_folder,
        ping_interval: float = PING_INTERVAL,
        call_backend_factory: BackendFactory = _default_backend_factory,
        voice_talker: Callable[[RepoSession], TalkerFn] = _default_voice_talker,
    ) -> None:
        self.call_backend_factory = call_backend_factory
        self.voice_talker = voice_talker
        self.config = config
        self.pairing = PairingCode()
        self.talker_factory = talker_factory
        self.worker_factory = worker_factory
        self.browse = browse
        self.ping_interval = ping_interval
        self.sessions: dict[str, RepoSession] = {}
        self.connections: set[Connection] = set()
        self.uploads = UploadStore(config.home)

    @property
    def allowed_origin(self) -> str:
        return f"chrome-extension://{self.config.extension_id}"

    # --- sessions ---

    def get_session(self, repo_path: Path) -> RepoSession:
        key = os.path.realpath(repo_path)
        session = self.sessions.get(key)
        if session is None:
            session = RepoSession(
                Path(key),
                self.config.home,
                emit=lambda msg, key=key: self.broadcast(key, msg),
                talker_factory=self.talker_factory,
                worker_factory=self.worker_factory,
                talker_model=self.config.talker_model,
                worker_model=self.config.worker_model,
            )
            self.sessions[key] = session
            session.start()
        return session

    def broadcast(self, key: str, msg: ServerMessage) -> None:
        session = self.sessions.get(key)
        for conn in list(self.connections):
            if conn.authenticated and conn.session is not None and conn.session is session:
                conn.send(msg)

    async def close(self) -> None:
        for conn in list(self.connections):
            await self.end_call(conn)
        for session in list(self.sessions.values()):
            await session.close()
        self.sessions.clear()

    # --- uploads (screenshots) ---

    async def upload(self, request: Request) -> JSONResponse:
        """`POST /api/upload`: raw image bytes in, `{"ref": "<32-hex>"}` out."""
        if request.headers.get("origin") != self.allowed_origin:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        auth = request.headers.get("authorization", "")
        scheme, _, given = auth.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            given.strip().encode(), self.config.token.encode()
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        media_type = media_type_of(request.headers.get("content-type"))
        if media_type is None:
            return JSONResponse({"error": "unsupported media type"}, status_code=415)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "too large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_UPLOAD_BYTES:
                return JSONResponse({"error": "too large"}, status_code=413)
        if not body:
            return JSONResponse({"error": "empty body"}, status_code=400)
        ref = await asyncio.to_thread(self.uploads.save, bytes(body), media_type)
        return JSONResponse({"ref": ref})

    # --- websocket ---

    async def websocket(self, ws: WebSocket) -> None:
        if ws.headers.get("origin") != self.allowed_origin:
            log.warning("rejected websocket from origin %r", ws.headers.get("origin"))
            await ws.close(code=CLOSE_FORBIDDEN)
            return
        await ws.accept()
        conn = Connection(ws)
        self.connections.add(conn)
        writer = asyncio.create_task(conn.writer())
        pinger = asyncio.create_task(conn.pinger(self.ping_interval))
        closing = False
        try:
            while True:
                raw = await ws.receive_text()
                if not await self.handle(conn, raw):
                    closing = True
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.connections.discard(conn)
            await self.end_call(conn)
            pinger.cancel()
            if closing:
                await _wait(writer, 2.0)  # flush the error, then close with the code
            writer.cancel()

    async def handle(self, conn: Connection, raw: str) -> bool:
        """Handle one client message. Returns False when the connection should close."""
        try:
            msg = parse_client(raw)
        except ValidationError as exc:
            conn.error("bad_message", _short(str(exc)))
            return True

        if isinstance(msg, Pong):
            return True
        if isinstance(msg, PairRequest):
            if self.pairing.consume(msg.payload.code):
                conn.send(PairResult(payload=PairResultPayload(token=self.config.token)))
            else:
                conn.error("bad_pairing_code", "That pairing code is wrong or has expired.")
            return True
        if isinstance(msg, Hello):
            if not secrets.compare_digest(msg.payload.token, self.config.token):
                conn.error("unauthorized", "Bad token. Pair the extension again.")
                conn.close(CLOSE_UNAUTHORIZED)
                return False
            conn.authenticated = True
            conn.clock_offset = now_ms() - msg.payload.clock_sample
            self.bind(conn, msg.payload.origin)
            return True
        if not conn.authenticated:
            conn.error("unauthorized", "Send hello first.")
            conn.close(CLOSE_UNAUTHORIZED)
            return False

        if isinstance(msg, RepoMap):
            self.map_repo(conn, msg.payload.origin, msg.payload.path)
        elif isinstance(msg, RepoBrowse):
            origin = normalise_origin(msg.payload.origin)
            if origin is None:
                conn.error("bad_origin", "Only http://localhost and http://127.0.0.1 origins.")
                return True
            path = await self.browse(origin)
            if path is None:
                conn.error("browse_cancelled", "No folder chosen.")
            else:
                self.map_repo(conn, origin, path)
        elif isinstance(
            msg,
            (UserText, TaskCancel, TaskReorder, SettingsUpdate, PickAdd, PickRemove, PicksClear),
        ):
            await self._repo_message(conn, msg)
        elif isinstance(msg, (CallStart, CallEnd, CallMute, WebRTCOffer, WebRTCIceIn)):
            await self._call_message(conn, msg)
        else:
            for session in self.sessions.values():
                if session.respond_approval(msg.payload.id, msg.payload.allow):
                    break
            else:
                conn.error("unknown_approval", "No pending approval with that id.")
        return True

    async def _repo_message(
        self,
        conn: Connection,
        msg: UserText
        | TaskCancel
        | TaskReorder
        | SettingsUpdate
        | PickAdd
        | PickRemove
        | PicksClear,
    ) -> None:
        session = conn.session
        if session is None:
            conn.error("no_repo", "This connection isn't bound to a repo.")
            return
        if isinstance(msg, UserText):
            text = msg.payload.text.strip()
            if text:
                session.submit_user_text(text, msg.payload.pick_ids, msg.payload.page)
        elif isinstance(msg, PickAdd):
            session.add_pick(msg.payload)
        elif isinstance(msg, PickRemove):
            session.remove_pick(msg.payload.id)
        elif isinstance(msg, PicksClear):
            session.clear_picks()
        elif isinstance(msg, TaskCancel):
            if not await session.cancel(msg.payload.id):
                conn.error("unknown_task", "No active task with that id.")
        elif isinstance(msg, TaskReorder):
            session.reorder(msg.payload.ids)
        elif msg.payload.preset is not None:
            session.set_preset(msg.payload.preset)

    # --- calls (SPEC §6.8) ---

    async def _call_message(
        self, conn: Connection, msg: CallStart | CallEnd | CallMute | WebRTCOffer | WebRTCIceIn
    ) -> None:
        if isinstance(msg, WebRTCIceIn):
            return  # non-trickle: the offer already carries every candidate
        if isinstance(msg, CallEnd):
            await self.end_call(conn)
            return
        if isinstance(msg, CallStart):
            session = conn.session
            if session is None:
                conn.error("no_repo", "This connection isn't bound to a repo.")
                return
            if conn.call is not None:
                conn.error("call_active", "A call is already active on this connection.")
                return
            conn.call = Call(
                session,
                conn.send,
                self.call_backend_factory,
                self.voice_talker(session),
                on_closed=lambda call: _forget_call(conn, call),
            )
            return
        call = conn.call
        if call is None:
            conn.error("no_call", "Send call.start first.")
            return
        if isinstance(msg, CallMute):
            call.set_muted(msg.payload.muted)
            return
        # The first offer can wait several seconds for the voice models to load; don't
        # block the socket's read loop meanwhile.
        task = asyncio.create_task(self._answer(conn, call, msg.payload.sdp))
        conn.call_tasks.add(task)
        task.add_done_callback(conn.call_tasks.discard)

    async def _answer(self, conn: Connection, call: Call, sdp: str) -> None:
        try:
            answer = await call.offer(sdp)
        except CallError as exc:
            conn.error(exc.code, str(exc))
            return
        except Exception as exc:
            log.exception("voice call failed to start")
            conn.error("call_failed", _short(f"The voice call couldn't start: {exc}"))
            await call.end()
            return
        if not call.ended:
            conn.send(WebRTCAnswer(payload=WebRTCAnswerPayload(sdp=answer)))

    async def end_call(self, conn: Connection) -> None:
        call, conn.call = conn.call, None
        if call is not None:
            await call.end()

    def bind(self, conn: Connection, raw_origin: str | None) -> None:
        origin = normalise_origin(raw_origin)
        if origin is None:
            conn.origin = None
            conn.session = None
            conn.error("no_origin", "The active tab isn't a localhost page.")
            return
        conn.origin = origin
        path = self.config.repos.get(origin)
        if path is None or not Path(path).is_dir():
            conn.session = None
            conn.send(RepoUnknown(payload=RepoUnknownPayload(origin=origin)))
            return
        self._welcome(conn, origin, self.get_session(Path(path)))

    def map_repo(self, conn: Connection, raw_origin: str, raw_path: str) -> None:
        origin = normalise_origin(raw_origin)
        if origin is None:
            conn.error("bad_origin", "Only http://localhost and http://127.0.0.1 origins.")
            return
        path = Path(os.path.expanduser(raw_path.strip()))
        if not raw_path.strip() or not path.is_absolute() or not path.is_dir():
            conn.error("bad_path", f"Not an existing folder: {raw_path}")
            return
        real = Path(os.path.realpath(path))
        self.config.map_repo(origin, real)
        conn.origin = origin
        self._welcome(conn, origin, self.get_session(real))

    def _welcome(self, conn: Connection, origin: str, session: RepoSession) -> None:
        if conn.call is not None and conn.call.session is not session:
            call, conn.call = conn.call, None
            task = asyncio.create_task(call.end())  # a call belongs to one repo
            conn.call_tasks.add(task)
            task.add_done_callback(conn.call_tasks.discard)
        conn.session = session
        conn.send(
            Welcome(
                payload=WelcomePayload(
                    repo=session.repo_info(origin),
                    state=session.state(),
                    clock_offset=conn.clock_offset,
                )
            )
        )


def _forget_call(conn: Connection, call: Call) -> None:
    if conn.call is call:
        conn.call = None


async def _wait(task: asyncio.Task[None], seconds: float) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(asyncio.shield(task), seconds)


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def create_app(daemon: Daemon, *, preload_voice: bool = False) -> Starlette:
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        with contextlib.suppress(Exception):
            removed = await asyncio.to_thread(daemon.uploads.prune)
            if removed:
                log.info("pruned %d old uploads", removed)
        preload: asyncio.Task[object] | None = None
        if preload_voice:
            from .voice.models import get_models

            preload = asyncio.create_task(get_models())
        yield
        if preload is not None and not preload.done():
            preload.cancel()
        await daemon.close()

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/api/upload", daemon.upload, methods=["POST"]),
            WebSocketRoute("/ws", daemon.websocket),
        ],
        lifespan=lifespan,
    )
