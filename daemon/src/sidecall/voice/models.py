"""Voice model loading and the single MLX executor (SPEC §6.8).

Parakeet (STT) and Kokoro (TTS) load once, lazily on the first call (or at startup with
`sidecall start --preload-voice`), and are shared by every call. All MLX work runs on one
worker thread, so it never runs concurrently on Metal and never blocks the event loop.

parakeet-mlx, mlx-audio and soxr ship no type information, so they are imported as
`Any` through `importlib` and wrapped here and in `services`.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

PARAKEET_REPO = os.environ.get("SIDECALL_PARAKEET_REPO", "mlx-community/parakeet-tdt-0.6b-v3")
KOKORO_REPO = os.environ.get("SIDECALL_KOKORO_REPO", "mlx-community/Kokoro-82M-bf16")
KOKORO_VOICE = os.environ.get("SIDECALL_KOKORO_VOICE", "af_heart")
STT_SAMPLE_RATE = 16000

MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")

Float32Array = npt.NDArray[np.float32]


async def run_mlx[T](fn: Callable[[], T]) -> T:
    return await asyncio.get_running_loop().run_in_executor(MLX_EXECUTOR, fn)


def lib(name: str) -> Any:
    """Import an untyped library as `Any`."""
    return importlib.import_module(name)


def resample(audio: Float32Array, src: int, dst: int) -> Float32Array:
    if src == dst:
        return audio
    out: Float32Array = np.asarray(lib("soxr").resample(audio, src, dst), dtype=np.float32)
    return out


@dataclass
class VoiceModels:
    parakeet: Any
    kokoro: Any
    load_secs: dict[str, float] = field(default_factory=dict[str, float])

    def transcribe(self, audio16k: Float32Array) -> Any:
        """Offline Parakeet on one segment (MLX thread only). Returns an AlignedResult."""
        mx = lib("mlx.core")
        get_logmel = lib("parakeet_mlx.audio").get_logmel
        mel = get_logmel(mx.array(audio16k), self.parakeet.preprocessor_config)
        return self.parakeet.generate(mel)[0]

    def synthesize(self, text: str, voice: str = KOKORO_VOICE, speed: float = 1.0) -> Any:
        """Kokoro generator of results with `.audio` and `.sample_rate` (MLX thread only)."""
        return self.kokoro.generate(
            text, voice=voice, speed=speed, lang_code="a", split_pattern=r"\n+"
        )


def _kokoro_audio(models: VoiceModels, text: str) -> tuple[Float32Array, int]:
    parts: list[Float32Array] = []
    sample_rate = 24000
    for res in models.synthesize(text):
        parts.append(np.asarray(res.audio, dtype=np.float32))
        sample_rate = int(res.sample_rate)
    return (np.concatenate(parts) if parts else np.zeros(1, np.float32)), sample_rate


def load_models_sync() -> VoiceModels:
    secs: dict[str, float] = {}
    t = time.perf_counter()
    parakeet = lib("parakeet_mlx").from_pretrained(PARAKEET_REPO)
    secs["parakeet_load"] = time.perf_counter() - t
    t = time.perf_counter()
    kokoro = lib("mlx_audio.tts.utils").load(KOKORO_REPO)
    secs["kokoro_load"] = time.perf_counter() - t
    models = VoiceModels(parakeet=parakeet, kokoro=kokoro, load_secs=secs)

    # Warm-up: first calls compile Metal kernels for each new input shape and load
    # spaCy and the voice pack. Several lengths, so the first real turn isn't slow.
    t = time.perf_counter()
    short, sr = _kokoro_audio(models, "Warming up.")
    long, _ = _kokoro_audio(models, "Warming up the voice with a longer sentence than before.")
    secs["kokoro_warmup"] = time.perf_counter() - t
    t = time.perf_counter()
    speech = resample(np.concatenate([short, long]), sr, STT_SAMPLE_RATE)
    for seconds in (0.6, 1.5, 3.0, 6.0):
        n = int(seconds * STT_SAMPLE_RATE)
        clip = np.resize(speech, n) if len(speech) < n else speech[:n]
        models.transcribe(clip.astype(np.float32))
    secs["parakeet_warmup"] = time.perf_counter() - t
    return models


_models: VoiceModels | None = None
_lock: asyncio.Lock | None = None


async def get_models() -> VoiceModels:
    """Load (once) and return the shared models."""
    global _models, _lock
    if _models is not None:
        return _models
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _models is None:
            t = time.perf_counter()
            _models = await run_mlx(load_models_sync)
            log.info(
                "voice models ready in %.1f s: %s",
                time.perf_counter() - t,
                {k: round(v, 2) for k, v in _models.load_secs.items()},
            )
        return _models
