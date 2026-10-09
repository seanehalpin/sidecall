"""Live voice loop: SmallWebRTC signalling + static browser client.

    uv run python server.py          # http://localhost:7860

Models are loaded and warmed once at startup and shared by all sessions.
Per connection: transport.input() -> Parakeet -> user agg (Silero + Smart Turn v3)
  -> StubTalker -> Kokoro -> transport.output().
Transcripts, bot text and per-turn latency are sent to the page over the
WebRTC data channel (SmallWebRTCConnection.send_app_message).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

import aioice.ice
import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import FileResponse
from loguru import logger

from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from bot import Event, build_worker
from voice_services import Models, StubTalker, load_models

HERE = Path(__file__).parent
STATE: dict = {}

# Offer only a loopback ICE candidate. aioice skips 127.0.0.1 by default and
# advertises LAN/IPv6 addresses instead; inbound UDP to those can be dropped by
# the macOS firewall (seen on the dev machine: browser checks never arrived and
# ICE timed out after 60 s). Loopback isn't filtered, and the browser is always
# on this machine, which also matches SPEC §6.1 (bind 127.0.0.1 only).
aioice.ice.get_host_addresses = lambda use_ipv4, use_ipv6: ["127.0.0.1"]
VAD_STOP_SECS = 0.2


class LiveMetrics:
    """Turns observer events into data-channel messages for the page."""

    def __init__(self, conn: SmallWebRTCConnection):
        self.conn = conn
        self.vad_stop: float | None = None
        self.turn_stop: float | None = None
        self.waiting_audio = False
        self.bot_text: list[str] = []

    def send(self, msg: dict):
        try:
            self.conn.send_app_message(msg)
        except Exception as e:  # pragma: no cover
            logger.debug(f"send_app_message failed: {e}")

    def __call__(self, ev: Event):
        if ev.name == "VADUserStartedSpeakingFrame":
            self.send({"type": "vad", "speaking": True})
        elif ev.name == "VADUserStoppedSpeakingFrame":
            self.vad_stop = ev.t
            self.send({"type": "vad", "speaking": False})
        elif ev.name == "InterruptionFrame":
            self.waiting_audio = False
            self.send({"type": "interruption"})
        elif ev.name == "transcription":
            words = [
                {
                    "w": w["word"],
                    "s": round(w["start"] - ev.data["result"]["segment_start"], 2),
                }
                for w in ev.data["result"]["words"]
            ]
            self.send(
                {
                    "type": "transcript",
                    "text": ev.data["text"],
                    "words": words,
                    "stt_ms": round(ev.data["result"]["stt_secs"] * 1000),
                }
            )
        elif ev.name == "smart_turn":
            self.send(
                {
                    "type": "smart_turn",
                    "complete": ev.data["complete"],
                    "p": round(ev.data["prob"], 3),
                    "ms": round(ev.data["ms"]),
                }
            )
        elif ev.name == "UserStoppedSpeakingFrame":
            self.turn_stop = ev.t
            self.waiting_audio = True
            self.bot_text = []
        elif ev.name == "llm_text":
            self.bot_text.append(ev.data["text"])
        elif ev.name == "out_audio" and self.waiting_audio:
            self.waiting_audio = False
            speech_end = (self.vad_stop or ev.t) - VAD_STOP_SECS
            self.send(
                {
                    "type": "latency",
                    # estimated end of speech = VAD stop - stop_secs
                    "e2e_ms": round((ev.t - speech_end) * 1000),
                    "turn_end_ms": round(
                        ((self.turn_stop or ev.t) - speech_end) * 1000
                    ),
                    "bot_text": "".join(self.bot_text),
                }
            )


async def run_session(conn: SmallWebRTCConnection):
    models: Models = STATE["models"]
    transport = SmallWebRTCTransport(
        webrtc_connection=conn,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
        ),
    )
    live = LiveMetrics(conn)
    talker = StubTalker(
        word_delay=float(STATE.get("word_delay", 0.0)),
        extra_sentences=int(STATE.get("extra", 0)),
    )
    worker, _obs, _ = build_worker(
        transport.input(), transport.output(), models, talker=talker, on_event=live
    )

    @transport.event_handler("on_client_connected")
    async def _connected(_t, _c):
        logger.info("client connected")
        live.send({"type": "ready", "t": time.time()})

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(_t, _c):
        logger.info("client disconnected")
        await worker.cancel()

    @transport.event_handler("on_app_message")
    async def _app_message(_t, message, *_):
        # Page can switch the stub to slow/long mode to test barge-in.
        if isinstance(message, dict) and message.get("type") == "config":
            talker._word_delay = float(message.get("word_delay", 0))
            talker._extra_sentences = int(message.get("extra", 0))
            logger.info(f"talker config: {message}")

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = time.perf_counter()
    STATE["models"] = await load_models()
    logger.info(
        f"models ready in {time.perf_counter() - t:.1f}s: {STATE['models'].load_secs}"
    )
    STATE["handler"] = SmallWebRTCRequestHandler()
    yield
    await STATE["handler"].close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    async def on_connection(conn: SmallWebRTCConnection):
        background_tasks.add_task(run_session, conn)

    return await STATE["handler"].handle_web_request(
        request=request, webrtc_connection_callback=on_connection
    )


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    await STATE["handler"].handle_patch_request(request)
    return {"status": "success"}


if __name__ == "__main__":
    import os

    uvicorn.run(app, host="localhost", port=int(os.environ.get("PORT", "7860")))
