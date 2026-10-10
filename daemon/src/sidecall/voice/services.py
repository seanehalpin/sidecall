"""Custom Pipecat services: Parakeet STT and Kokoro TTS on MLX (SPEC §6.8).

Ported from spike 6 (`spikes/voice/voice_services.py`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import numpy as np
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

from .models import (
    KOKORO_REPO,
    KOKORO_VOICE,
    MLX_EXECUTOR,
    PARAKEET_REPO,
    STT_SAMPLE_RATE,
    Float32Array,
    VoiceModels,
    resample,
    run_mlx,
)

log = logging.getLogger(__name__)


@dataclass
class Word:
    """A recognised word. Times are `time.monotonic()` seconds (daemon clock)."""

    word: str
    start: float
    end: float
    confidence: float


def tokens_to_words(tokens: list[Any], offset: float = 0.0) -> list[Word]:
    """Group Parakeet SentencePiece sub-word tokens into words.

    A token whose text starts with a space begins a new word. Confidence is the
    geometric mean over the word's tokens.
    """
    groups: list[tuple[str, float, float, list[float]]] = []
    for tok in tokens:
        text = str(tok.text)
        start, end, conf = float(tok.start), float(tok.end), float(tok.confidence)
        if not groups or text.startswith(" "):
            groups.append((text.strip(), start + offset, end + offset, [conf]))
        else:
            word, s, _e, confs = groups[-1]
            groups[-1] = (word + text, s, end + offset, [*confs, conf])
    return [
        Word(word, s, e, float(np.exp(np.mean(np.log(np.array(confs) + 1e-10)))))
        for word, s, e, confs in groups
        if word
    ]


class ParakeetSTTService(SegmentedSTTService):
    """Offline Parakeet on each VAD segment.

    `TranscriptionFrame.result` is `{"words": list[Word], "segment_start": float,
    "stt_secs": float, "audio_secs": float}` with daemon-clock (monotonic) times, ready
    for marker placement (SPEC §6.9).
    """

    def __init__(self, *, models: VoiceModels, **kwargs: Any) -> None:
        kwargs.setdefault("ttfs_p99_latency", 0.6)
        kwargs.setdefault("settings", STTSettings(model=PARAKEET_REPO, language="en"))
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._models = models
        self._last_audio_time = 0.0
        self._segment_starts: deque[float] = deque()

    @property
    def wants_wav_segments(self) -> bool:
        return False  # raw int16 PCM

    def can_generate_metrics(self) -> bool:
        return True

    async def process_audio_frame(
        self, frame: InputAudioRawFrame, direction: FrameDirection
    ) -> None:
        self._last_audio_time = time.monotonic()
        await super().process_audio_frame(frame, direction)

    async def _handle_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame) -> None:
        # Time of the segment's first sample, before the base class drains the buffer.
        buffer: bytearray = self._audio_buffer  # pyright: ignore[reportPrivateUsage]
        if self.is_usable and buffer:
            buffered_secs = len(buffer) / (self.sample_rate * 2)
            self._segment_starts.append(self._last_audio_time - buffered_secs)
        await super()._handle_user_stopped_speaking(frame)  # pyright: ignore[reportPrivateUsage]

    def _transcribe_sync(self, pcm: bytes) -> tuple[Any, float, float]:
        audio: Float32Array = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        audio = resample(audio, self.sample_rate, STT_SAMPLE_RATE)
        t = time.perf_counter()
        result = self._models.transcribe(audio)
        return result, time.perf_counter() - t, len(audio) / STT_SAMPLE_RATE

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame]:
        seg_start = self._segment_starts.popleft() if self._segment_starts else time.monotonic()
        await self.start_processing_metrics()
        try:
            result, stt_secs, audio_secs = await run_mlx(lambda: self._transcribe_sync(audio))
        except Exception as exc:  # pragma: no cover
            yield ErrorFrame(error=f"parakeet failed: {exc}")
            return
        await self.stop_processing_metrics()
        text = str(result.text).strip()
        if not text:
            return
        log.debug("parakeet %.0f ms / %.2f s audio: %s", stt_secs * 1000, audio_secs, text)
        yield TranscriptionFrame(
            text,
            self._user_id,
            time_now_iso8601(),
            result={
                "words": tokens_to_words(list(result.tokens), offset=seg_start),
                "segment_start": seg_start,
                "stt_secs": stt_secs,
                "audio_secs": audio_secs,
            },
        )


def trim_leading_silence(
    audio: Float32Array, sample_rate: int, keep: float = 0.03, threshold: float = 0.01
) -> Float32Array:
    """Kokoro puts ~0.3 s of near-silence before speech; cut it, keeping `keep` seconds."""
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if len(loud) == 0:
        return audio
    return audio[max(0, int(loud[0]) - int(keep * sample_rate)) :]


_Item = tuple[Float32Array, int] | Exception | None


class KokoroTTSService(TTSService):
    """Kokoro-82M via mlx-audio. One audio frame per Kokoro segment."""

    def __init__(
        self,
        *,
        models: VoiceModels,
        voice: str = KOKORO_VOICE,
        speed: float = 1.0,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("settings", TTSSettings(model=KOKORO_REPO, voice=voice, language="en"))
        super().__init__(push_start_frame=True, push_stop_frames=True, **kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._models = models
        self._voice = voice
        self._speed = speed

    def can_generate_metrics(self) -> bool:
        return True

    def _generate_into(
        self,
        text: str,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[_Item],
        cancelled: list[bool],
    ) -> None:
        try:
            for res in self._models.synthesize(text, self._voice, self._speed):
                if cancelled[0]:
                    break
                item = (np.asarray(res.audio, dtype=np.float32), int(res.sample_rate))
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # pragma: no cover
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        cancelled = [False]
        await self.start_tts_usage_metrics(text)
        loop.run_in_executor(MLX_EXECUTOR, self._generate_into, text, loop, queue, cancelled)
        first = True
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    yield ErrorFrame(error=f"kokoro failed: {item}")
                    break
                audio, sr = item
                if first:
                    audio = trim_leading_silence(audio, sr)
                    first = False
                await self.stop_ttfb_metrics()
                audio = resample(audio, sr, self.sample_rate)
                pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
                yield TTSAudioRawFrame(
                    audio=pcm, sample_rate=self.sample_rate, num_channels=1, context_id=context_id
                )
        finally:
            cancelled[0] = True  # stop between segments if interrupted
            await self.stop_ttfb_metrics()
