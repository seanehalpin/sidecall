"""Shared pipeline builder for the live server and the offline harness.

transport.input() -> STT(Parakeet) -> user aggregator (Silero VAD + Smart Turn v3)
  -> StubTalker -> TTS(Kokoro) -> transport.output() -> assistant aggregator
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TurnMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from voice_services import (
    KokoroMLXTTSService,
    Models,
    ParakeetMLXSTTService,
    StubTalker,
)

TRACKED = (
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    InterruptionFrame,
    TranscriptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    MetricsFrame,
)


@dataclass
class Event:
    t: float  # time.monotonic()
    name: str
    data: dict = field(default_factory=dict)


class LatencyObserver(BaseObserver):
    """Records the first push of each tracked frame (by frame id) with a
    monotonic timestamp. 'out_audio' marks audio actually written by the output
    transport (frames it pushes downstream after a successful write)."""

    def __init__(
        self, output: FrameProcessor, on_event: Callable[[Event], None] | None = None
    ):
        super().__init__()
        self.events: list[Event] = []
        self._seen: set[int] = set()
        self._output = output
        self._on_event = on_event

    def _add(self, name: str, **data):
        ev = Event(time.monotonic(), name, data)
        self.events.append(ev)
        if self._on_event:
            self._on_event(ev)

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if not isinstance(f, TRACKED):
            return
        if data.source is self._output and isinstance(f, TTSAudioRawFrame):
            self._add("out_audio", bytes=len(f.audio))
            self._seen.add(f.id)
            return
        if f.id in self._seen:
            return
        # Broadcast frames come as two sibling instances; dedupe on sibling too.
        sib = getattr(f, "broadcast_sibling_id", None)
        if sib is not None and sib in self._seen:
            self._seen.add(f.id)
            return
        self._seen.add(f.id)
        if isinstance(f, TTSAudioRawFrame):
            self._add("tts_audio", bytes=len(f.audio))
        elif isinstance(f, LLMTextFrame):
            self._add("llm_text", text=f.text)
        elif isinstance(f, TranscriptionFrame):
            self._add("transcription", text=f.text, result=f.result)
        elif isinstance(f, MetricsFrame):
            for d in f.data:
                if isinstance(d, TurnMetricsData):
                    self._add(
                        "smart_turn",
                        complete=d.is_complete,
                        prob=d.probability,
                        ms=d.e2e_processing_time_ms,
                    )
        else:
            self._add(type(f).__name__, src=str(data.source))


def build_worker(
    transport_input: FrameProcessor,
    transport_output: FrameProcessor,
    models: Models,
    *,
    talker: StubTalker | None = None,
    on_event: Callable[[Event], None] | None = None,
    idle_timeout_secs: float | None = None,
) -> tuple[PipelineWorker, LatencyObserver, StubTalker]:
    stt = ParakeetMLXSTTService(model=models.parakeet)
    tts = KokoroMLXTTSService(model=models.kokoro)
    talker = talker or StubTalker()

    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            user_turn_strategies=UserTurnStrategies(
                stop=[
                    TurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3()
                    )
                ]
            ),
        ),
    )

    pipeline = Pipeline(
        [transport_input, stt, user_agg, talker, tts, transport_output, assistant_agg]
    )
    observer = LatencyObserver(transport_output, on_event)
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
        ),
        observers=[observer],
        enable_rtvi=False,
        idle_timeout_secs=idle_timeout_secs,
    )
    return worker, observer, talker


__all__ = ["build_worker", "Event", "LatencyObserver", "FrameDirection"]
