"""Slow: the real voice pipeline, headless (loads Parakeet, Kokoro, Silero, Smart Turn).

Run with `uv run pytest --voice -s tests/test_voice_pipeline.py`. Like the spike harness,
a fake input transport plays a Kokoro-generated utterance at real-time pace and a fake
output transport "plays" audio at real-time pace.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner
from test_call import FakeSession, states

from sidecall.protocol.messages import ServerMessage
from sidecall.voice.call import Call, echo_talker
from sidecall.voice.models import STT_SAMPLE_RATE, get_models, resample, run_mlx
from sidecall.voice.pipeline import IN_SAMPLE_RATE, OUT_SAMPLE_RATE, build_worker

pytestmark = pytest.mark.voice

CHUNK = 0.02
Int16Array = npt.NDArray[np.int16]


class TapeInput(BaseInputTransport):
    """Pushes 20 ms frames at real-time pace: queued audio, silence otherwise."""

    def __init__(self, params: TransportParams) -> None:
        super().__init__(params)  # pyright: ignore[reportUnknownMemberType]
        self._queue: asyncio.Queue[tuple[Int16Array, asyncio.Future[None]]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._task = self.create_task(self._pump(), "tape")  # pyright: ignore[reportUnknownMemberType]

    async def cancel(self, frame: Any) -> None:
        if self._task:
            await self.cancel_task(self._task)  # pyright: ignore[reportUnknownMemberType]
        await super().cancel(frame)

    def play(self, pcm: Int16Array) -> asyncio.Future[None]:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((pcm, fut))
        return fut

    async def _pump(self) -> None:
        n = int(IN_SAMPLE_RATE * CHUNK)
        silence = np.zeros(n, np.int16)
        deadline = time.monotonic()
        current: tuple[Int16Array, asyncio.Future[None]] | None = None
        pos = 0
        while True:
            if current is None and not self._queue.empty():
                current, pos = self._queue.get_nowait(), 0
            chunk = silence
            if current is not None:
                chunk = current[0][pos : pos + n]
                if len(chunk) < n:
                    chunk = np.concatenate([chunk, silence[: n - len(chunk)]])
            deadline += CHUNK
            delay = deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            await self.push_audio_frame(
                InputAudioRawFrame(
                    audio=chunk.tobytes(), sample_rate=IN_SAMPLE_RATE, num_channels=1
                )
            )
            if current is not None:
                pos += n
                if pos >= len(current[0]):
                    current[1].set_result(None)
                    current = None


class PacedOutput(BaseOutputTransport):
    def __init__(self, params: TransportParams) -> None:
        super().__init__(params)  # pyright: ignore[reportUnknownMemberType]
        self._until = 0.0

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        now = time.monotonic()
        if self._until > now:
            await asyncio.sleep(self._until - now)
        self._until = max(self._until, time.monotonic()) + len(frame.audio) / 2 / frame.sample_rate
        return True


class NoMedia:
    """The test drives the pipeline itself; the Call only needs a backend to exist."""

    async def start(self, offer_sdp: str) -> str:
        return ""

    def set_muted(self, muted: bool) -> None:
        pass

    async def stop(self) -> None:
        pass


async def _utterance(text: str) -> Int16Array:
    models = await get_models()

    def synth() -> Int16Array:
        parts = [np.asarray(r.audio, dtype=np.float32) for r in models.synthesize(text)]
        audio = resample(np.concatenate(parts), 24000, STT_SAMPLE_RATE)
        lead = np.zeros(int(0.5 * STT_SAMPLE_RATE), np.float32)
        return (np.clip(np.concatenate([lead, audio]), -1, 1) * 32767).astype(np.int16)

    return await run_mlx(synth)


async def test_pipeline_headless_turn() -> None:
    t0 = time.perf_counter()
    models = await get_models()
    print(f"\nmodels ready in {time.perf_counter() - t0:.1f} s: {models.load_secs}")
    sentences = [
        "Make the card header red and drop the shadow.",
        "Increase the padding on the list items.",
    ]
    clips = [await _utterance(s) for s in sentences]

    params = TransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=IN_SAMPLE_RATE,
        audio_out_enabled=True,
        audio_out_sample_rate=OUT_SAMPLE_RATE,
    )
    tape, out = TapeInput(params), PacedOutput(params)
    session = FakeSession()
    sent: list[ServerMessage] = []
    call = Call(session, sent.append, lambda _e, _t: NoMedia(), echo_talker)
    call.connected()
    worker = build_worker(tape, out, models, echo_talker, call)
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _started(_w: Any, _f: Frame) -> None:  # pyright: ignore[reportUnusedFunction]
        started.set()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), 30)
        await asyncio.sleep(1.0)
        for clip in clips:
            await tape.play(clip)
            await asyncio.sleep(4.5)
    finally:
        await worker.cancel()
        await asyncio.wait_for(run, 10)

    turns = [t for r, t, _ in session.entries if r == "user"]
    replies = [t for r, t, _ in session.entries if r == "agent"]
    print("turns:", turns)
    print("latencies (ms):", [round(x) for x in call.latencies])
    print("states:", states(sent))
    assert len(turns) >= 2
    joined = " ".join(turns).lower()
    assert "card header" in joined and "padding" in joined
    assert replies and all(r.startswith("You said:") for r in replies)
    assert states(sent)[:5] == ["connecting", "listening", "thinking", "speaking", "listening"]
    assert any(m.type == "transcript.partial" for m in sent)
    assert call.latencies and min(call.latencies) < 1500


async def test_backend_answers_non_trickle_offer_on_loopback() -> None:
    """PipecatBackend over real aiortc: answer has only 127.0.0.1 candidates and connects."""
    from aiortc import (  # pyright: ignore[reportMissingTypeStubs]
        RTCPeerConnection,
        RTCSessionDescription,
    )
    from aiortc.mediastreams import AudioStreamTrack  # pyright: ignore[reportMissingTypeStubs]

    from sidecall.voice.pipeline import PipecatBackend

    session = FakeSession()
    sent: list[ServerMessage] = []
    call = Call(session, sent.append, PipecatBackend, echo_talker)
    pc: Any = RTCPeerConnection()
    pc.addTrack(AudioStreamTrack())
    await pc.setLocalDescription(await pc.createOffer())
    try:
        answer = await asyncio.wait_for(call.offer(pc.localDescription.sdp), 60)
        candidates = [ln for ln in answer.splitlines() if ln.startswith("a=candidate")]
        assert candidates and all(" 127.0.0.1 " in c for c in candidates)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
        for _ in range(100):
            if call.state == "listening":
                break
            await asyncio.sleep(0.05)
        assert call.state == "listening"
    finally:
        await call.end()
        await pc.close()
    assert states(sent)[-1] == "idle"
