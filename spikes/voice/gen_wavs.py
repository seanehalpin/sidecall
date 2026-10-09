"""Generate test utterances with Kokoro (mlx-audio) plus ground-truth word
timestamps from Kokoro's predicted durations.

Writes out/utt_XX.wav (16 kHz mono int16, with LEAD_SILENCE of leading silence)
and out/utterances.json with per-word [start, end] in seconds relative to the
start of the WAV, plus energy-based speech onset/offset.

    uv run python gen_wavs.py
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
import soxr

OUT = Path(__file__).parent / "out"
LEAD_SILENCE = 0.5

UTTERANCES = [
    "Make the card header red and drop the shadow.",
    "Can you move the sidebar to the right side of the page?",
    "Increase the padding on the list items to sixteen pixels.",
    "Change the submit button label to save changes.",
    "Make this title bigger and use a bolder font weight.",
    "Swap the order of the first two cards in the grid.",
    "Add a subtle border under the navigation bar.",
    "Why is the form field misaligned with its label?",
]

VOICES = ["af_heart", "am_michael", "af_bella", "bm_george"]


def energy_bounds(
    x: np.ndarray, sr: int, thresh_db: float = -45.0
) -> tuple[float, float]:
    win = int(sr * 0.01)
    n = len(x) // win
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    idx = np.where(db > thresh_db)[0]
    return float(idx[0] * win / sr), float((idx[-1] + 1) * win / sr)


def write_wav(path: Path, x: np.ndarray, sr: int):
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main():
    from mlx_audio.tts.utils import load

    OUT.mkdir(exist_ok=True)
    tts = load("mlx-community/Kokoro-82M-bf16")
    pipe = tts._get_pipeline("a")  # KokoroPipeline: exposes per-token start_ts/end_ts
    meta = []
    for i, text in enumerate(UTTERANCES):
        voice = VOICES[i % len(VOICES)]
        if voice.startswith("b"):
            pipe_v = tts._get_pipeline("b")
        else:
            pipe_v = pipe
        audio_parts, words, t0 = [], [], 0.0
        for res in pipe_v(text, voice=voice, speed=1.0, split_pattern=None):
            a = np.array(
                res.audio[0] if res.audio.ndim > 1 else res.audio, dtype=np.float32
            )
            for tk in res.tokens or []:
                if getattr(tk, "start_ts", None) is None or not tk.phonemes:
                    continue
                words.append(
                    {"word": tk.text, "start": t0 + tk.start_ts, "end": t0 + tk.end_ts}
                )
            audio_parts.append(a)
            t0 += len(a) / 24000
        a24 = np.concatenate(audio_parts)
        a16 = soxr.resample(a24, 24000, 16000).astype(np.float32)
        a16 = np.concatenate([np.zeros(int(16000 * LEAD_SILENCE), np.float32), a16])
        for w in words:
            w["start"] += LEAD_SILENCE
            w["end"] += LEAD_SILENCE
        on, off = energy_bounds(a16, 16000)
        path = OUT / f"utt_{i:02d}.wav"
        write_wav(path, a16, 16000)
        meta.append(
            {
                "file": path.name,
                "text": text,
                "voice": voice,
                "duration": len(a16) / 16000,
                "speech_onset": on,
                "speech_offset": off,
                "words": words,
            }
        )
        print(
            f"{path.name} {voice:10s} {len(a16) / 16000:.2f}s onset={on:.2f} offset={off:.2f} {text}"
        )
    (OUT / "utterances.json").write_text(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
