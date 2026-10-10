"""The Pipecat voice pipeline and its WebRTC backend (SPEC §6.8).

```
transport.input() → MuteGate → Parakeet STT → SegmentTap
  → user aggregator (Silero VAD + Smart Turn v3) → TalkerProcessor → Kokoro TTS
  → transport.output() → OutputTap → assistant aggregator
```

Turn assembly (SPEC §6.9) happens in `Call`: `SegmentTap` hands it each STT segment's
word times, and `TalkerProcessor` reports the aggregator's turn end.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from . import loopback
from .call import CallEvents, ReplyStream
from .models import VoiceModels, get_models
from .services import KokoroTTSService, ParakeetSTTService
from .talker import OutputTap, SegmentTap, TalkerProcessor

loopback.apply()

log = logging.getLogger(__name__)

IN_SAMPLE_RATE = 16000
OUT_SAMPLE_RATE = 24000
VAD_STOP_SECS = 0.2
VAD_START_SECS = float(os.environ.get("SIDECALL_VAD_START_SECS", "0.2"))
"""Speech needed before VAD (and so barge-in) triggers. Pipecat's default."""


class MuteGate(FrameProcessor):
    """`call.mute`: replace mic audio with silence, so STT and VAD hear nothing.

    Silence (rather than dropping frames) lets VAD close an open segment normally.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self.muted = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self.muted and isinstance(frame, InputAudioRawFrame):
            frame = InputAudioRawFrame(
                audio=bytes(len(frame.audio)),
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
            )
        await self.push_frame(frame, direction)


class CallObserver(BaseObserver):
    """Reports bot speaking and measures per-turn latency (observers run on their own
    tasks, so anything order-sensitive goes through the taps instead).

    Latency is end of speech (VAD stop minus `stop_secs`) to the first audio the output
    transport writes after the turn ends.
    """

    def __init__(self, events: CallEvents, output: FrameProcessor) -> None:
        super().__init__()  # pyright: ignore[reportUnknownMemberType]
        self._events = events
        self._output = output
        self._seen: set[int] = set()
        self._vad_stop: float | None = None
        self._turn_stop: float | None = None
        self._waiting = False

    def _first_sight(self, frame: Frame) -> bool:
        if frame.id in self._seen:
            return False
        self._seen.add(frame.id)
        sibling = frame.broadcast_sibling_id
        if sibling is not None:
            if sibling in self._seen:
                return False
            self._seen.add(sibling)
        return True

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        now = time.monotonic()
        if isinstance(frame, TTSAudioRawFrame):
            if data.source is self._output and self._waiting:
                self._waiting = False
                speech_end = (self._vad_stop or now) - VAD_STOP_SECS
                e2e = (now - speech_end) * 1000
                turn_end = ((self._turn_stop or now) - speech_end) * 1000
                log.info(
                    "voice turn latency: %.0f ms to first audio (turn end %.0f ms)", e2e, turn_end
                )
                self._events.latency(e2e)
            return
        if not isinstance(
            frame,
            (
                VADUserStoppedSpeakingFrame,
                UserStoppedSpeakingFrame,
                InterruptionFrame,
                BotStartedSpeakingFrame,
                BotStoppedSpeakingFrame,
            ),
        ) or not self._first_sight(frame):
            return
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self._vad_stop = now
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._turn_stop = now
            self._waiting = True
        elif isinstance(frame, InterruptionFrame):
            self._waiting = False
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._events.bot_speaking(True)
        else:
            self._events.bot_speaking(False)


@dataclass
class VoicePipeline:
    worker: PipelineWorker
    talker: TalkerProcessor


def build_worker(
    transport_input: FrameProcessor,
    transport_output: FrameProcessor,
    models: VoiceModels,
    events: CallEvents,
    *,
    gate: MuteGate | None = None,
) -> VoicePipeline:
    stt = ParakeetSTTService(models=models)
    tts = KokoroTTSService(models=models)
    user_agg, assistant_agg = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(start_secs=VAD_START_SECS, stop_secs=VAD_STOP_SECS)
            ),
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
            ),
        ),
    )
    talker = TalkerProcessor(events)
    processors: list[FrameProcessor] = [
        transport_input,
        gate or MuteGate(),
        stt,
        SegmentTap(events),
        user_agg,
        talker,
        tts,
        transport_output,
        OutputTap(events),
        assistant_agg,
    ]
    worker = PipelineWorker(
        Pipeline(processors),
        params=PipelineParams(
            audio_in_sample_rate=IN_SAMPLE_RATE,
            audio_out_sample_rate=OUT_SAMPLE_RATE,
            enable_metrics=True,
        ),
        observers=[CallObserver(events, transport_output)],
        enable_rtvi=False,
    )
    return VoicePipeline(worker, talker)


class PipecatBackend:
    """`CallBackend` over Pipecat's SmallWebRTC transport (non-trickle signalling)."""

    def __init__(self, events: CallEvents) -> None:
        self._events = events
        self._gate = MuteGate()
        self._conn: SmallWebRTCConnection | None = None
        self._worker: PipelineWorker | None = None
        self._talker: TalkerProcessor | None = None
        self._run: asyncio.Task[None] | None = None

    async def start(self, offer_sdp: str) -> str:
        models = await get_models()
        conn = SmallWebRTCConnection()
        self._conn = conn
        # aiortc gathers every (loopback) candidate in setLocalDescription, so the
        # answer is complete and no trickle ICE is needed.
        await conn.initialize(sdp=offer_sdp, type="offer")
        transport = SmallWebRTCTransport(
            webrtc_connection=conn,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=IN_SAMPLE_RATE,
                audio_out_sample_rate=OUT_SAMPLE_RATE,
            ),
        )
        events = self._events

        @transport.event_handler("on_client_connected")
        async def _connected(_t: Any, _c: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            events.connected()

        @transport.event_handler("on_client_disconnected")
        async def _disconnected(_t: Any, _c: Any) -> None:  # pyright: ignore[reportUnusedFunction]
            events.closed()

        pipeline = build_worker(
            transport.input(), transport.output(), models, events, gate=self._gate
        )
        self._worker, self._talker = pipeline.worker, pipeline.talker
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(self._worker)
        self._run = asyncio.create_task(runner.run())
        answer = conn.get_answer()
        if answer is None:
            raise RuntimeError("no SDP answer")
        return str(answer["sdp"])

    def set_muted(self, muted: bool) -> None:
        self._gate.muted = muted

    def speak(self, reply: ReplyStream, *, interrupt: bool = False) -> None:
        if self._talker is None:
            asyncio.get_running_loop().create_task(reply.aclose())
            return
        self._talker.speak(reply, interrupt=interrupt)

    async def stop(self) -> None:
        if self._worker is not None:
            await self._worker.cancel()
        if self._run is not None:
            try:
                await asyncio.wait_for(self._run, 10)
            except Exception:
                self._run.cancel()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.disconnect()
