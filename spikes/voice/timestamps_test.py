"""Parakeet word timestamps: offline vs streaming, against Kokoro ground truth.

    uv run python gen_wavs.py   # once
    uv run python timestamps_test.py [--chunk 0.5] [--verbose]

Offline: get_logmel(whole wav) -> model.generate() -> AlignedResult tokens.
Streaming: model.transcribe_stream(context_size=(256,256)) fed CHUNK-second
pieces via add_audio(); read .result tokens (finalized + draft).
"""

from __future__ import annotations

import argparse
import json
import re
import time
import wave
from pathlib import Path

import mlx.core as mx
import numpy as np
from parakeet_mlx import from_pretrained
from parakeet_mlx.audio import get_logmel

from voice_services import PARAKEET_REPO, tokens_to_words

OUT = Path(__file__).parent / "out"


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return (
            np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
            / 32768
        )


def norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def match(pred: list[dict], truth: list[dict]) -> list[tuple[dict, dict]]:
    """Greedy in-order match on normalised word text."""
    pairs, j = [], 0
    for t in truth:
        for k in range(j, min(j + 3, len(pred))):
            if norm(pred[k]["word"]) == norm(t["word"]):
                pairs.append((pred[k], t))
                j = k + 1
                break
    return pairs


def stats(errs: list[float]) -> str:
    a = np.abs(np.array(errs)) * 1000
    s = np.array(errs) * 1000
    return (
        f"n={len(a)} mean|err|={a.mean():.0f}ms median|err|={np.median(a):.0f}ms "
        f"p90|err|={np.percentile(a, 90):.0f}ms max={a.max():.0f}ms bias={s.mean():+.0f}ms"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=float, default=0.5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    meta = json.loads((OUT / "utterances.json").read_text())
    model = from_pretrained(PARAKEET_REPO)
    cfg = model.preprocessor_config
    model.generate(get_logmel(mx.array(np.zeros(16000, np.float32)), cfg))  # warm
    print(f"token time resolution: {model.time_ratio * 1000:.0f} ms (encoder frame)")

    off_start, off_end, st_start, st_raw_start = [], [], [], []
    for m in meta:
        audio = read_wav(OUT / m["file"])
        truth = m["words"]

        # ---- offline: whole utterance ----
        t = time.perf_counter()
        res = model.generate(get_logmel(mx.array(audio), cfg))[0]
        off_ms = (time.perf_counter() - t) * 1000
        words = tokens_to_words(res.tokens)
        pairs = match(words, truth)
        off_start += [p["start"] - g["start"] for p, g in pairs]
        off_end += [p["end"] - g["end"] for p, g in pairs]

        # ---- streaming: chunked add_audio ----
        n = int(args.chunk * 16000)
        chunk_ms = []
        snapshots = []
        with model.transcribe_stream(context_size=(256, 256)) as s:
            for i in range(0, len(audio), n):
                t = time.perf_counter()
                s.add_audio(mx.array(audio[i : i + n]))
                chunk_ms.append((time.perf_counter() - t) * 1000)
                snapshots.append(
                    (
                        (i + n) / 16000,
                        [tok.start for tok in s.finalized_tokens],
                        [tok.start for tok in s.draft_tokens],
                    )
                )
            sres = s.result
        swords = tokens_to_words(sres.tokens)
        spairs = match(swords, truth)
        st_start += [p["start"] - g["start"] for p, g in spairs]
        st_raw_start += [p["start"] for p, _ in spairs]

        print(
            f"\n{m['file']} offline {off_ms:.0f}ms: {res.text!r}\n"
            f"           stream  chunk={args.chunk}s mean {np.mean(chunk_ms):.0f}ms/chunk: {sres.text!r}"
        )
        if args.verbose or m is meta[0]:
            print("   word          truth_start  offline_start  stream_start")
            sp = {id(g): p for p, g in spairs}
            for p, g in pairs:
                s_ = sp.get(id(g))
                print(
                    f"   {g['word']:<12} {g['start']:8.3f}   {p['start']:10.3f}   "
                    f"{(s_['start'] if s_ else float('nan')):10.3f}"
                )
            if m is meta[0]:
                print(
                    "   streaming token starts after each add_audio (finalized | draft):"
                )
                for t_end, fin, dr in snapshots[:8]:
                    print(
                        f"     fed {t_end:4.1f}s  fin={[round(x, 2) for x in fin][-6:]}  "
                        f"draft={[round(x, 2) for x in dr][:6]}"
                    )

    print("\n=== Word START error vs Kokoro ground truth ===")
    print("offline  :", stats(off_start))
    print("offline end:", stats(off_end))
    print("streaming:", stats(st_start) if st_start else "no matches")
    if st_raw_start:
        print(f"streaming max token start seen: {max(st_raw_start):.2f}s")


if __name__ == "__main__":
    main()
