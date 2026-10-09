"""Streaming timestamps on a long (~34 s) concatenation, to see whether token
times stay global once the stream starts finalizing (dropping) frames.

    uv run python stream_long_test.py
"""

import json

import mlx.core as mx
import numpy as np
from parakeet_mlx import from_pretrained
from parakeet_mlx.audio import get_logmel

from timestamps_test import OUT, match, read_wav, stats
from voice_services import PARAKEET_REPO, tokens_to_words

meta = json.loads((OUT / "utterances.json").read_text())
audio, truth, t0 = [], [], 0.0
for m in meta:
    a = read_wav(OUT / m["file"])
    audio.append(a)
    truth += [{**w, "start": w["start"] + t0, "end": w["end"] + t0} for w in m["words"]]
    t0 += len(a) / 16000
audio = np.concatenate(audio)
print(f"total {len(audio) / 16000:.1f}s, {len(truth)} words")
model = from_pretrained(PARAKEET_REPO)

res = model.generate(get_logmel(mx.array(audio), model.preprocessor_config))[0]
p = match(tokens_to_words(res.tokens), truth)
print("offline whole 34s     :", stats([a["start"] - b["start"] for a, b in p]))

for ctx in [(256, 256), (128, 32), (64, 16)]:
    with model.transcribe_stream(context_size=ctx) as s:
        for i in range(0, len(audio), 8000):
            s.add_audio(mx.array(audio[i : i + 8000]))
        words = tokens_to_words(s.result.tokens)
        nfin = len(s.finalized_tokens)
    p = match(words, truth)
    errs = [a["start"] - b["start"] for a, b in p]
    print(
        f"stream ctx={ctx} drop={ctx[1] * 0.08:.1f}s finalized_tokens={nfin}: matched {len(p)}/{len(truth)}",
        stats(errs) if errs else "",
    )
    late = [(b["word"], round(b["start"], 2), round(a["start"], 2)) for a, b in p][-4:]
    print("   last words (word, truth, stream):", late)

print("\n--- detail ctx=(128, 32) ---")
with model.transcribe_stream(context_size=(128, 32)) as s:
    for i in range(0, len(audio), 8000):
        s.add_audio(mx.array(audio[i : i + 8000]))
    r = s.result
print("text:", r.text[:300])
ws = tokens_to_words(r.tokens)
print(
    "first/last words with start:",
    [(w["word"], round(w["start"], 2)) for w in ws[:6]],
    "...",
    [(w["word"], round(w["start"], 2)) for w in ws[-6:]],
)
print(
    "max token start:", max(t.start for t in r.tokens), "audio len:", len(audio) / 16000
)
