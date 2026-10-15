"""Slow: the real voice pipeline, headless (loads Parakeet, Kokoro, Silero, Smart Turn).

Run with `uv run pytest --voice -s tests/test_voice_pipeline.py`. Like the spike harness,
a fake input transport plays Kokoro-generated speech at real-time pace and a fake output
transport "plays" audio at real-time pace.

The live test (`--voice --integration`) also talks to Claude (haiku) and measures the
end-of-speech → first-audio latency with the real talker.
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
import statistics
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner
from test_call import FakeSession, script, states

from sidecall.announcer import QUIET_MS
from sidecall.protocol.messages import ServerMessage
from sidecall.voice.call import Call, ReplyStream
from sidecall.voice.models import STT_SAMPLE_RATE, VoiceModels, get_models, resample, run_mlx
from sidecall.voice.pipeline import IN_SAMPLE_RATE, OUT_SAMPLE_RATE, VoicePipeline, build_worker
from sidecall.voice.talker import TalkerProcessor
from sidecall.voice.turns import PickTime, monotonic_to_daemon_ms

pytestmark = pytest.mark.voice

CHUNK = 0.02
LEAD = 0.5
Int16Array = npt.NDArray[np.int16]
Float32Array = npt.NDArray[np.float32]


class TapeInput(BaseInputTransport):
    """Pushes 20 ms frames at real-time pace: queued audio, silence otherwise."""

    def __init__(self, params: TransportParams) -> None:
        super().__init__(params)  # pyright: ignore[reportUnknownMemberType]
        self._queue: asyncio.Queue[_Clip] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._task = self.create_task(self._pump(), "tape")  # pyright: ignore[reportUnknownMemberType]

    async def cancel(self, frame: Any) -> None:
        if self._task:
            await self.cancel_task(self._task)  # pyright: ignore[reportUnknownMemberType]
        await super().cancel(frame)

    def play(self, pcm: Int16Array) -> _Clip:
        loop = asyncio.get_running_loop()
        clip = _Clip(pcm, loop.create_future(), loop.create_future())
        self._queue.put_nowait(clip)
        return clip

    async def _pump(self) -> None:
        n = int(IN_SAMPLE_RATE * CHUNK)
        silence = np.zeros(n, np.int16)
        deadline = time.monotonic()
        current: _Clip | None = None
        pos = 0
        while True:
            if current is None and not self._queue.empty():
                current, pos = self._queue.get_nowait(), 0
            chunk = silence
            if current is not None:
                chunk = current.pcm[pos : pos + n]
                if len(chunk) < n:
                    chunk = np.concatenate([chunk, silence[: n - len(chunk)]])
            deadline += CHUNK
            delay = deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if current is not None and pos == 0:
                # The first sample of this chunk "happens" one chunk before it's pushed.
                current.started.set_result(time.monotonic() - CHUNK)
            await self.push_audio_frame(
                InputAudioRawFrame(
                    audio=chunk.tobytes(), sample_rate=IN_SAMPLE_RATE, num_channels=1
                )
            )
            if current is not None:
                pos += n
                if pos >= len(current.pcm):
                    current.done.set_result(None)
                    current = None


@dataclass
class _Clip:
    pcm: Int16Array
    started: asyncio.Future[float]
    done: asyncio.Future[None]


class PacedOutput(BaseOutputTransport):
    def __init__(self, params: TransportParams) -> None:
        super().__init__(params)  # pyright: ignore[reportUnknownMemberType]
        self._until = 0.0
        self.writes: list[float] = []
        """`time.monotonic()` when each audio chunk finished playing."""

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        now = time.monotonic()
        if self._until > now:
            await asyncio.sleep(self._until - now)
        self._until = max(self._until, time.monotonic()) + len(frame.audio) / 2 / frame.sample_rate
        if np.any(np.frombuffer(frame.audio, np.int16)):
            self.writes.append(self._until)
        return True


class HeadlessBackend:
    """The test drives the pipeline itself; replies go to its TalkerProcessor."""

    def __init__(self) -> None:
        self.talker: TalkerProcessor | None = None

    async def start(self, offer_sdp: str) -> str:
        return ""

    def set_muted(self, muted: bool) -> None:
        pass

    def speak(self, reply: ReplyStream, *, interrupt: bool = False) -> None:
        assert self.talker is not None
        self.talker.speak(reply, interrupt=interrupt)

    async def stop(self) -> None:
        pass


# --- audio helpers ---------------------------------------------------------------------


async def synth(text: str, voice: str = "af_heart") -> Float32Array:
    """Kokoro speech at 16 kHz, trimmed to the speech (no leading/trailing silence)."""
    models = await get_models()

    def run() -> Float32Array:
        parts = [np.asarray(r.audio, dtype=np.float32) for r in models.synthesize(text, voice)]
        audio = resample(np.concatenate(parts), 24000, STT_SAMPLE_RATE)
        loud = np.flatnonzero(np.abs(audio) > 0.01)
        return audio[int(loud[0]) : int(loud[-1]) + 1] if len(loud) else audio

    return await run_mlx(run)


def silence(seconds: float) -> Float32Array:
    return np.zeros(int(seconds * STT_SAMPLE_RATE), np.float32)


def pcm(*parts: Float32Array) -> Int16Array:
    audio = np.concatenate(parts)
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16)


def secs(audio: Float32Array) -> float:
    return len(audio) / STT_SAMPLE_RATE


@dataclass
class Rig:
    models: VoiceModels
    tape: TapeInput
    out: PacedOutput
    call: Call
    pipeline: VoicePipeline
    sent: list[ServerMessage]


async def run_rig(
    session: Any, body: Callable[[Rig], Awaitable[None]], *, warm: float = 1.0
) -> Rig:
    models = await get_models()
    params = TransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=IN_SAMPLE_RATE,
        audio_out_enabled=True,
        audio_out_sample_rate=OUT_SAMPLE_RATE,
    )
    tape, out = TapeInput(params), PacedOutput(params)
    sent: list[ServerMessage] = []
    backend = HeadlessBackend()
    call = Call(session, sent.append, lambda _e: backend)
    pipeline = build_worker(tape, out, models, call)
    backend.talker = pipeline.talker
    started = asyncio.Event()

    @pipeline.worker.event_handler("on_pipeline_started")
    async def _started(_w: Any, _f: Frame) -> None:  # pyright: ignore[reportUnusedFunction]
        started.set()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(pipeline.worker)
    run = asyncio.create_task(runner.run())
    rig = Rig(models, tape, out, call, pipeline, sent)
    try:
        await asyncio.wait_for(started.wait(), 30)
        call.connected()
        await asyncio.sleep(warm)
        await body(rig)
    finally:
        await call.end()
        await pipeline.worker.cancel()
        await asyncio.wait_for(run, 10)
    return rig


def user_entries(session: FakeSession) -> list[str]:
    return [t for r, t, _ in session.entries if r == "user"]


# --- tests -----------------------------------------------------------------------------


async def test_pipeline_headless_turn() -> None:
    t0 = time.perf_counter()
    models = await get_models()
    print(f"\nmodels ready in {time.perf_counter() - t0:.1f} s: {models.load_secs}")
    clips = [
        pcm(silence(LEAD), await synth("Make the card header red and drop the shadow.")),
        pcm(silence(LEAD), await synth("Increase the padding on the list items.")),
    ]
    session = FakeSession()

    async def body(rig: Rig) -> None:
        for clip in clips:
            await rig.tape.play(clip).done
            await asyncio.sleep(4.5)

    rig = await run_rig(session, body)
    turns = user_entries(session)
    replies = [t for r, t, _ in session.entries if r == "agent"]
    print("turns:", turns)
    print("latencies (ms):", [round(x) for x in rig.call.latencies])
    print("states:", states(rig.sent))
    assert len(turns) >= 2
    joined = " ".join(turns).lower()
    assert "card header" in joined and "padding" in joined
    assert replies and all(r.startswith("You said:") for r in replies)
    assert states(rig.sent)[:5] == ["connecting", "listening", "thinking", "speaking", "listening"]
    assert any(m.type == "transcript.partial" for m in rig.sent)
    assert any(m.type == "agent.text.delta" for m in session.emitted)
    assert rig.call.latencies and min(rig.call.latencies) < 1500


async def test_spoken_turns_place_pick_markers() -> None:
    """Two WAVs with picks injected at known times (SPEC §6.9, PLAN M3 acceptance):

    1. "Make this" ⟨0.45 s pause, pick 1⟩ "match that one." ⟨pick 2 0.15 s later⟩: a
       turn split across VAD segments (and usually across a Smart Turn end), a pick in
       the pause and one just after the last word. Depending on timing the picks hold
       the turn or arrive late and re-submit it.
    2. ⟨pick 4 four seconds before, pick 3 one second before⟩ "Put a border around it.":
       pick 3 is prefixed; pick 4 is outside the window and stays unbound.
    Picks reach the "daemon" 300 ms after they're made, like a screenshot upload.
    """
    a = await synth("Make this")
    b = await synth("match that one.")
    c = await synth("Put a border around it.")
    gap = 0.45
    clip1 = pcm(silence(LEAD), a, silence(gap), b, silence(0.1))
    clip2 = pcm(silence(LEAD), c, silence(0.1))
    session = FakeSession()
    loop = asyncio.get_running_loop()
    offset = time.time() - time.monotonic()

    def pick_at(rig: Rig, pick_id: int, t_mono: float) -> None:
        pick = PickTime(pick_id, monotonic_to_daemon_ms(t_mono, offset_s=offset))

        def arrive() -> None:
            session.picks.append(pick)
            rig.call.pick_added(pick)

        loop.call_at(max(loop.time(), t_mono + 0.3), arrive)

    async def body(rig: Rig) -> None:
        clip = rig.tape.play(clip1)
        t = await clip.started
        pick_at(rig, 1, t + LEAD + secs(a) + 0.15)
        pick_at(rig, 2, t + LEAD + secs(a) + gap + secs(b) + 0.15)
        await clip.done
        await asyncio.sleep(6.0)
        now = time.monotonic()
        lead_in = 4.5  # pick 4 at speech minus 4 s, pick 3 at speech minus 1 s
        pick_at(rig, 4, now + lead_in - 4.0)
        pick_at(rig, 3, now + lead_in - 1.0)
        await asyncio.sleep(lead_in - LEAD - CHUNK)
        await rig.tape.play(clip2).done
        await asyncio.sleep(4.0)

    rig = await run_rig(session, body)
    turns = user_entries(session)
    print("\nturns:", turns, "picks:", session.user_picks, "replaced:", session.replaced)
    print("requests:", session.requests)
    assert len(turns) == 2, turns
    first, second = (t.lower() for t in turns)
    assert first.startswith("make this [1]"), first
    assert first.endswith("one [2]."), first
    assert second.startswith("[3] put a border"), second
    assert "[4]" not in second
    assert session.user_picks[-2:] == [[1, 2], [3]]
    # The talker got the marked text with the bound picks.
    assert session.requests[-1][1] == [3]
    assert rig.call.latencies


async def test_interruption_stops_audio_fast() -> None:
    """Barge in on a long reply; measure user speech onset → last bot audio played."""
    long_reply = " ".join(
        f"Sentence number {i} of a long story about cards and sidebars." for i in range(1, 15)
    )
    session = FakeSession()

    async def slow(_t: str) -> AsyncGenerator[str, None]:
        async for word in script(long_reply):  # still streaming when the user barges in
            await asyncio.sleep(0.04)
            yield word

    session.reply_text = slow
    ask = await synth("Tell me a long story.")
    stop = await synth("Wait, stop.")
    results: dict[str, float] = {}

    async def body(rig: Rig) -> None:
        await rig.tape.play(pcm(silence(LEAD), ask)).done
        for _ in range(100):  # wait until the bot has been speaking a while
            await asyncio.sleep(0.1)
            if rig.out.writes and time.monotonic() - rig.out.writes[0] > 2.0:
                break
        clip = rig.tape.play(pcm(silence(LEAD), stop))
        onset = await clip.started + LEAD
        await clip.done
        await asyncio.sleep(3.0)
        writes = [w for w in rig.out.writes if w >= onset - 0.5]
        # Bot audio stops at the first gap after the onset (a new reply may follow).
        last = onset
        for prev, nxt in itertools.pairwise(writes):
            last = prev
            if nxt - prev > 0.25:
                break
        results["stop_ms"] = (last - onset) * 1000
        cancelled = [c for c in rig.pipeline.talker.cancelled_at if c >= onset]
        results["cancel_ms"] = (cancelled[0] - onset) * 1000 if cancelled else float("nan")

    await run_rig(session, body)
    agent = [t for r, t, _ in session.entries if r == "agent"]
    print(f"\ninterruption: audio stopped {results['stop_ms']:.0f} ms after speech onset")
    print(f"talker reply cancelled {results['cancel_ms']:.0f} ms after onset")
    print("agent entries:", agent)
    assert agent and agent[0].endswith(" …")
    assert long_reply.startswith(agent[0].removesuffix(" …").split(" Sentence number 2")[0])
    assert len(agent[0]) < len(long_reply)
    assert session.interruptions and session.interruptions[0]
    assert results["stop_ms"] < 600


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
    call = Call(session, sent.append, PipecatBackend)
    pc: Any = RTCPeerConnection()
    pc.addTrack(AudioStreamTrack())
    # Like the offscreen document: a data channel in the offer, so Pipecat's app
    # messages reach the client instead of timing out.
    channel: Any = pc.createDataChannel("sidecall")
    received: list[str] = []
    channel.on("message", received.append)
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
        backend: Any = call.backend
        conn: Any = backend._conn
        conn.send_app_message({"type": "probe"})
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.05)
        assert received and '"probe"' in received[0]
    finally:
        await call.end()
        await pc.close()
    assert states(sent)[-1] == "idle"


# --- live: the real talker (haiku) -------------------------------------------------------

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "svelte5-app"


class QuickWorker:
    """Completes every task after a second, without Claude."""

    def __init__(self, session: Any) -> None:
        pass

    async def run(self, task: Any, on_event: Any) -> Any:
        from sidecall.session import WorkerResult

        await asyncio.sleep(1.0)
        return WorkerResult("completed", summary="Made the heading red.", files=["x.svelte"])

    async def interrupt(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.mark.integration
async def test_live_talker_latency_and_announcement(tmp_path: Path) -> None:
    from sidecall.session import RepoSession
    from sidecall.talker import ClaudeTalker

    repo = tmp_path / "app"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("node_modules", ".svelte-kit"))
    emitted: list[ServerMessage] = []
    talkers: list[ClaudeTalker] = []

    def make_talker(s: RepoSession) -> ClaudeTalker:
        talkers.append(ClaudeTalker(s))
        return talkers[-1]

    session = RepoSession(
        repo,
        tmp_path / "home",
        emitted.append,
        make_talker,
        QuickWorker,
        talker_model="haiku",
    )
    session.start()
    lines = [
        "Hi there, can you hear me okay?",
        "What kind of app is this?",
        "How many items are in the list on the home page?",
        "Okay. Make the main heading on the home page red.",
        "Great, thanks. What should we change next?",
    ]
    clips = [pcm(silence(LEAD), await synth(line)) for line in lines]

    async def wait_idle(rig: Rig, limit: float) -> None:
        deadline = time.monotonic() + limit
        await asyncio.sleep(1.0)
        while time.monotonic() < deadline:
            busy = rig.call._agent is not None or session.talker_busy  # pyright: ignore[reportPrivateUsage]
            if not busy and rig.call.state == "listening":
                return
            await asyncio.sleep(0.2)

    async def body(rig: Rig) -> None:
        for i, clip in enumerate(clips):
            await rig.tape.play(clip).done
            await wait_idle(rig, 30)
            if i == 3:
                # The dispatched task completes ~1 s later; the talker should announce
                # it once the user has been quiet for 1.2 s.
                for _ in range(150):
                    if rig.call.announcements:
                        break
                    await asyncio.sleep(0.1)
                await wait_idle(rig, 30)
            await asyncio.sleep(0.8)

    try:
        rig = await run_rig(session, body, warm=1.5)
    finally:
        await session.close()
    lat = sorted(rig.call.latencies)
    timings = [t for tk in talkers for t in tk.timings]
    # total_cost_usd is cumulative per client.
    cost = sum(max((t.cost_usd or 0) for t in tk.timings) for tk in talkers if tk.timings)
    print("\ntranscript:")
    for e in session.transcript.recent():
        print(f"  {e.role}: {e.text}")
    print("latencies (ms):", [round(x) for x in rig.call.latencies])
    if lat:
        p90 = lat[min(len(lat) - 1, round(0.9 * (len(lat) - 1)))]
        print(f"median {statistics.median(lat):.0f} ms, p90 {p90:.0f} ms")
    for t in timings:
        print(
            f"  talker spoken={t.spoken} first_delta={t.first_delta} first_release="
            f"{t.first_release} narration_release={t.narration_release} result={t.result} "
            f"cost={t.cost_usd}"
        )
    print(f"talker cost ${cost:.4f}")
    assert len(lat) >= 4
    assert statistics.median(lat) <= 1500
    assert rig.call.announcements >= 1
    quiet = [q for q in rig.call.quiet_before_announcements if q is not None]
    print("quiet before announcements (ms):", [round(q) for q in quiet])
    assert quiet and min(quiet) >= QUIET_MS
    agent_texts = [e.text for e in session.transcript.recent() if e.role == "agent"]
    assert len(agent_texts) >= len(lines) + 1
