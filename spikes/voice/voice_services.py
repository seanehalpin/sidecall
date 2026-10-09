"""Custom Pipecat services for the voice spike.

- ParakeetMLXSTTService: SegmentedSTTService running Parakeet TDT 0.6B v3 via
  parakeet-mlx, with word-level timestamps (absolute, monotonic clock) attached
  to every TranscriptionFrame.result.
- KokoroMLXTTSService: TTSService running Kokoro-82M via mlx-audio.
- StubTalker: a FrameProcessor standing in for the future TalkerProcessor. It
  answers each user turn with streamed "You said: ..." LLMTextFrames from a
  background task, and cancels that task on InterruptionFrame.

All MLX inference runs on ONE dedicated worker thread (MLX_EXECUTOR) so the
event loop is never blocked and STT/TTS never run MLX concurrently.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import soxr
from loguru import logger

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

PARAKEET_REPO = os.environ.get("PARAKEET_REPO", "mlx-community/parakeet-tdt-0.6b-v3")
KOKORO_REPO = os.environ.get("KOKORO_REPO", "mlx-community/Kokoro-82M-bf16")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")

# One thread for all MLX work (see module docstring).
MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")


async def run_mlx(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(MLX_EXECUTOR, lambda: fn(*args, **kwargs))


# --------------------------------------------------------------------------
# Model loading
# --------------------------------------------------------------------------


@dataclass
class Models:
    parakeet: Any
    kokoro: Any
    load_secs: dict[str, float] = field(default_factory=dict)


def _load_models_sync() -> Models:
    import mlx.core as mx
    from mlx_audio.tts.utils import load as load_tts
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel

    secs: dict[str, float] = {}
    t = time.perf_counter()
    parakeet = from_pretrained(PARAKEET_REPO)
    secs["parakeet_load"] = time.perf_counter() - t

    t = time.perf_counter()
    kokoro = load_tts(KOKORO_REPO)
    secs["kokoro_load"] = time.perf_counter() - t

    # Warm-up: first calls compile Metal kernels, load spaCy / voice pack.
    t = time.perf_counter()
    warm = list(
        kokoro.generate("Warming up the voice.", voice=KOKORO_VOICE, lang_code="a")
    )
    secs["kokoro_warmup"] = time.perf_counter() - t
    audio = np.array(warm[0].audio, dtype=np.float32)
    t = time.perf_counter()
    a16 = soxr.resample(audio, kokoro.sample_rate, 16000)
    parakeet.generate(get_logmel(mx.array(a16), parakeet.preprocessor_config))
    secs["parakeet_warmup"] = time.perf_counter() - t
    return Models(parakeet=parakeet, kokoro=kokoro, load_secs=secs)


async def load_models() -> Models:
    return await run_mlx(_load_models_sync)


# --------------------------------------------------------------------------
# Word timestamps
# --------------------------------------------------------------------------


def tokens_to_words(tokens, offset: float = 0.0) -> list[dict]:
    """Group Parakeet sub-word AlignedTokens into words.

    SentencePiece tokens that start a word carry a leading space in .text.
    Returns [{"word", "start", "end", "confidence"}] with offset added.
    """
    words: list[dict] = []
    for tok in tokens:
        text = tok.text
        if not words or text.startswith(" "):
            words.append(
                {
                    "word": text.strip(),
                    "start": tok.start + offset,
                    "end": tok.end + offset,
                    "confidence": [tok.confidence],
                }
            )
        else:
            w = words[-1]
            w["word"] += text
            w["end"] = tok.end + offset
            w["confidence"].append(tok.confidence)
    for w in words:
        c = w["confidence"]
        w["confidence"] = float(np.exp(np.mean(np.log(np.array(c) + 1e-10))))
    return [w for w in words if w["word"]]


# --------------------------------------------------------------------------
# STT
# --------------------------------------------------------------------------


class ParakeetMLXSTTService(SegmentedSTTService):
    """Segmented STT using parakeet-mlx. One transcription per VAD segment.

    TranscriptionFrame.result = {
        "words": [{"word", "start", "end", "confidence"}],   # monotonic seconds
        "segment_start": float,  # monotonic time of first sample in the segment
        "stt_secs": float,       # MLX inference wall time
        "audio_secs": float,
    }
    """

    def __init__(self, *, model, **kwargs):
        # Finalized transcripts trigger the turn immediately, so the p99 value
        # only matters as a fallback; set it to silence the warning.
        kwargs.setdefault("ttfs_p99_latency", 0.6)
        kwargs.setdefault("settings", STTSettings(model=PARAKEET_REPO, language="en"))
        super().__init__(**kwargs)
        self._model = model
        self._last_audio_time = 0.0
        self._segment_starts: deque[float] = deque()

    @property
    def wants_wav_segments(self) -> bool:
        return False  # raw int16 PCM

    def can_generate_metrics(self) -> bool:
        return True

    async def process_audio_frame(self, frame, direction):
        self._last_audio_time = time.monotonic()
        await super().process_audio_frame(frame, direction)

    async def _handle_user_stopped_speaking(self, frame: VADUserStoppedSpeakingFrame):
        # Wall-clock (monotonic) time of the first buffered sample, computed
        # before the base class drains the buffer into the segment queue.
        if self.is_usable and self._audio_buffer:
            buffered_secs = len(self._audio_buffer) / (self.sample_rate * 2)
            self._segment_starts.append(self._last_audio_time - buffered_secs)
        await super()._handle_user_stopped_speaking(frame)

    def _transcribe_sync(self, pcm: bytes):
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if self.sample_rate != 16000:
            audio = soxr.resample(audio, self.sample_rate, 16000)
        t = time.perf_counter()
        mel = get_logmel(mx.array(audio), self._model.preprocessor_config)
        result = self._model.generate(mel)[0]
        return result, time.perf_counter() - t, len(audio) / 16000

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        seg_start = (
            self._segment_starts.popleft() if self._segment_starts else time.monotonic()
        )
        await self.start_processing_metrics()
        try:
            result, stt_secs, audio_secs = await run_mlx(self._transcribe_sync, audio)
        except Exception as e:  # pragma: no cover
            yield ErrorFrame(error=f"parakeet failed: {e}")
            return
        await self.stop_processing_metrics()
        text = result.text.strip()
        if not text:
            return
        words = tokens_to_words(result.tokens, offset=seg_start)
        logger.debug(
            f"Parakeet [{stt_secs * 1000:.0f} ms / {audio_secs:.2f}s audio]: {text}"
        )
        yield TranscriptionFrame(
            text,
            self._user_id,
            time_now_iso8601(),
            result={
                "words": words,
                "segment_start": seg_start,
                "stt_secs": stt_secs,
                "audio_secs": audio_secs,
            },
        )


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------


class KokoroMLXTTSService(TTSService):
    """Kokoro-82M via mlx-audio. Yields one audio frame per Kokoro segment."""

    def __init__(
        self,
        *,
        model,
        voice: str = KOKORO_VOICE,
        speed: float = 1.0,
        trim_leading_silence: bool = True,
        **kwargs,
    ):
        kwargs.setdefault(
            "settings", TTSSettings(model=KOKORO_REPO, voice=voice, language="en")
        )
        super().__init__(push_start_frame=True, push_stop_frames=True, **kwargs)
        self._model = model
        self._voice = voice
        self._speed = speed
        self._trim = trim_leading_silence

    @staticmethod
    def _trim_lead(
        audio: np.ndarray, sr: int, keep: float = 0.03, thresh: float = 0.01
    ) -> np.ndarray:
        """Kokoro emits ~0.3 s of near-silence before speech; cut it (keep 30 ms)."""
        idx = np.flatnonzero(np.abs(audio) > thresh)
        if len(idx) == 0:
            return audio
        return audio[max(0, idx[0] - int(keep * sr)) :]

    def can_generate_metrics(self) -> bool:
        return True

    def _generate_into(
        self, text: str, loop, queue: asyncio.Queue, cancelled: list[bool]
    ):
        try:
            # split_pattern splits text into independently synthesised segments,
            # so long sentences get audio out sooner (first clause first).
            for res in self._model.generate(
                text,
                voice=self._voice,
                speed=self._speed,
                lang_code="a",
                split_pattern=r"\n+",
            ):
                if cancelled[0]:
                    break
                audio = np.array(res.audio, dtype=np.float32)
                loop.call_soon_threadsafe(queue.put_nowait, (audio, res.sample_rate))
        except Exception as e:  # pragma: no cover
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def run_tts(
        self, text: str, context_id: str
    ) -> AsyncGenerator[Frame | None, None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancelled = [False]
        await self.start_tts_usage_metrics(text)
        loop.run_in_executor(
            MLX_EXECUTOR, self._generate_into, text, loop, queue, cancelled
        )
        first_chunk = True
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    yield ErrorFrame(error=f"kokoro failed: {item}")
                    break
                audio, sr = item
                if first_chunk and self._trim:
                    audio = self._trim_lead(audio, sr)
                first_chunk = False
                await self.stop_ttfb_metrics()
                if sr != self.sample_rate:
                    audio = soxr.resample(audio, sr, self.sample_rate)
                pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
                yield TTSAudioRawFrame(
                    audio=pcm,
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
        finally:
            cancelled[0] = True  # stop the generator between segments if interrupted
            await self.stop_ttfb_metrics()


# --------------------------------------------------------------------------
# Stub LLM / TalkerProcessor prototype
# --------------------------------------------------------------------------


def _last_user_text(context) -> str:
    for msg in reversed(context.get_messages()):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict)
                )
    return ""


class StubTalker(FrameProcessor):
    """Echo LLM shaped like the future TalkerProcessor.

    - On LLMContextFrame: start a background response task (so this processor
      keeps receiving frames, including InterruptionFrame, while it streams).
    - On InterruptionFrame: cancel the in-flight task (where the real talker
      would also call ClaudeSDKClient.interrupt()), then forward the frame.

    word_delay: seconds between streamed words (simulates LLM token pacing).
    extra_sentences: extra filler sentences to make a long response.
    """

    def __init__(
        self,
        *,
        word_delay: float = 0.0,
        extra_sentences: int = 0,
        events: list | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._word_delay = word_delay
        self._extra_sentences = extra_sentences
        self._task: asyncio.Task | None = None
        self.events = events if events is not None else []

    def _mark(self, name: str, **kw):
        self.events.append((time.monotonic(), name, kw))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            if getattr(frame, "speculation", False):
                return  # never answer speculative contexts in the spike
            text = _last_user_text(frame.context)
            await self._cancel("superseded")
            self._mark("talker_context", text=text)
            self._task = self.create_task(self._respond(text), "stub_talker_respond")
        elif isinstance(frame, InterruptionFrame):
            await self._cancel("interruption")
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self):
        await self._cancel("cleanup")
        await super().cleanup()

    async def _cancel(self, reason: str):
        if self._task and not self._task.done():
            self._mark("talker_cancel_requested", reason=reason)
            await self.cancel_task(self._task)
            self._mark("talker_cancelled", reason=reason)
            # Real talker: await client.interrupt() here.
        self._task = None

    async def _respond(self, text: str):
        reply = f"You said: {text.strip()}"
        if not reply.endswith((".", "!", "?")):
            reply += "."
        for i in range(self._extra_sentences):
            reply += f" This is filler sentence number {i + 1}, which keeps me talking."
        words = re.findall(r"\S+\s*", reply)
        try:
            await self.push_frame(LLMFullResponseStartFrame())
            for i, w in enumerate(words):
                if i == 0:
                    self._mark("talker_first_text")
                await self.push_frame(LLMTextFrame(w if w.endswith(" ") else w + " "))
                if self._word_delay:
                    await asyncio.sleep(self._word_delay)
            await self.push_frame(LLMFullResponseEndFrame())
            self._mark("talker_done")
        except asyncio.CancelledError:
            # Don't push LLMFullResponseEndFrame: it would make TTS flush any
            # partially aggregated sentence after the interruption.
            self._mark("talker_task_cancelled_inside")
            raise
