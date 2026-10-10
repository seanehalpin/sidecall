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

SKEW_WINDOW_FRAMES = 250
"""About 5 s of 20 ms input frames."""
TRAILING_KEEP_SECS = 0.2
"""Silence kept after the last speech in a segment. Parakeet TDT stretches word times
into trailing silence: with Pipecat's default 0.5 s pad plus VAD's 0.2 s the end of the
last word came out ~0.35 s late; with 0.1-0.25 s it was within ~50 ms (measured)."""


def trim_trailing_silence(audio: Float32Array, sample_rate: int) -> Float32Array:
    """Cut a segment to TRAILING_KEEP_SECS after its last speech (20 ms RMS windows)."""
    win = max(1, int(0.02 * sample_rate))
    n = len(audio) // win
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * win].reshape(n, win) ** 2, axis=1))
    loud = np.flatnonzero(rms > max(0.004, float(rms.max()) * 0.06))
    if len(loud) == 0:
        return audio
    end = (int(loud[-1]) + 1) * win + int(TRAILING_KEEP_SECS * sample_rate)
    return audio[: min(len(audio), end)]


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
    geometric mean over the word's tokens. Punctuation tokens join the word's text but
    not its end time: TDT gives a trailing "." the pause after the word (measured: the
    end of "this." 240 ms past the speech), which would misplace `[n]` markers.
    """
    groups: list[tuple[str, float, float, list[float]]] = []
    for tok in tokens:
        text = str(tok.text)
        start, end, conf = float(tok.start), float(tok.end), float(tok.confidence)
        if not groups or text.startswith(" "):
            groups.append((text.strip(), start + offset, end + offset, [conf]))
        else:
            word, s, e, confs = groups[-1]
            punct = not any(ch.isalnum() for ch in text)
            groups[-1] = (word + text, s, e if punct else end + offset, [*confs, conf])
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
        kwargs.setdefault("trailing_silence_secs", 0.0)  # see TRAILING_KEEP_SECS
        kwargs.setdefault("settings", STTSettings(model=PARAKEET_REPO, language="en"))
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._models = models
        self._samples = 0
        # Per frame: arrival time minus audio time. Frames can reach this processor late
        # (a busy loop while a reply starts), never early, so the minimum over the last
        # few seconds is the audio clock's offset; arrival times alone skewed a later
        # segment's words by ~250 ms in testing.
        self._skew: deque[float] = deque(maxlen=SKEW_WINDOW_FRAMES)
        self._segment_starts: deque[float] = deque()

    @property
    def wants_wav_segments(self) -> bool:
        return False  # raw int16 PCM

    def can_generate_metrics(self) -> bool:
        return True

    async def process_audio_frame(
        self, frame: InputAudioRawFrame, direction: FrameDirection
    ) -> None:
        self._samples += len(frame.audio) // 2
        self._skew.append(time.monotonic() - self._samples / self.sample_rate)
        await super().process_audio_frame(frame, direction)

    def audio_time(self, sample: int) -> float:
        """Monotonic time of an input sample index (see `_skew`)."""
        anchor = (
            min(self._skew) if self._skew else time.monotonic() - self._samples / self.sample_rate
        )
        return anchor + sample / self.sample_rate

    async def _handle_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame) -> None:
        # Time of the segment's first sample, before the base class drains the buffer.
        buffer: bytearray = self._audio_buffer  # pyright: ignore[reportPrivateUsage]
        if self.is_usable and buffer:
            self._segment_starts.append(self.audio_time(self._samples - len(buffer) // 2))
        await super()._handle_user_stopped_speaking(frame)  # pyright: ignore[reportPrivateUsage]

    def _transcribe_sync(self, pcm: bytes) -> tuple[Any, float, float]:
        audio: Float32Array = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        audio = trim_trailing_silence(
            resample(audio, self.sample_rate, STT_SAMPLE_RATE), STT_SAMPLE_RATE
        )
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
