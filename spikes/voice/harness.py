"""Headless pipeline harness (no mic, no browser).

Feeds Kokoro-generated WAVs (gen_wavs.py) through the real Pipecat pipeline
(Silero VAD -> Smart Turn v3 -> Parakeet -> StubTalker -> Kokoro) at real-time
pace via a fake input transport, with a fake output transport that "plays"
audio at real-time pace. Measures latency and interruption behaviour.

    uv run python gen_wavs.py
    uv run python harness.py            # latency (8 utts) + interruption + memory
    uv run python harness.py --only latency|interrupt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame, StartFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

from bot import Event, build_worker
from voice_services import StubTalker, load_models

OUT = Path(__file__).parent / "out"
SR_IN = 16000
CHUNK = 0.02  # 20 ms input frames, like a WebRTC/mic source


def rss_mb() -> float:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
    )
    return int(out.stdout.strip()) / 1024


def footprint_mb() -> float | None:
    """macOS phys_footprint (includes Metal/IOKit memory that RSS misses)."""
    import re

    try:
        out = subprocess.run(
            ["footprint", str(os.getpid())], capture_output=True, text=True
        ).stdout
        m = re.search(r"Footprint: ([\d.]+) (KB|MB|GB)", out)
        if not m:
            return None
        return float(m.group(1)) * {"KB": 1 / 1024, "MB": 1, "GB": 1024}[m.group(2)]
    except Exception:
        return None


# --------------------------------------------------------------------------
# Fake transports
# --------------------------------------------------------------------------


class TapeInputTransport(BaseInputTransport):
    """Pushes 20 ms InputAudioRawFrames at real-time pace. Plays queued
    utterances, silence otherwise. Records when each sample index of an
    utterance was made available to the pipeline (end of its 20 ms chunk)."""

    def __init__(self, params: TransportParams):
        super().__init__(params)
        self._queue: asyncio.Queue[tuple[np.ndarray, asyncio.Future]] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._task = self.create_task(self._pump(), "tape_pump")

    async def stop(self, frame):
        if self._task:
            await self.cancel_task(self._task)
        await super().stop(frame)

    async def cancel(self, frame):
        if self._task:
            await self.cancel_task(self._task)
        await super().cancel(frame)

    def play(self, pcm: np.ndarray) -> asyncio.Future:
        """Queue int16 audio; future resolves to the per-chunk push times."""
        fut = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((pcm, fut))
        return fut

    async def _pump(self):
        n = int(SR_IN * CHUNK)
        silence = np.zeros(n, np.int16)
        deadline = time.monotonic()
        current: tuple[np.ndarray, asyncio.Future] | None = None
        pos, push_times = 0, []
        while True:
            if current is None and not self._queue.empty():
                current, pos, push_times = self._queue.get_nowait(), 0, []
            if current is not None:
                chunk = current[0][pos : pos + n]
                if len(chunk) < n:
                    chunk = np.concatenate([chunk, silence[: n - len(chunk)]])
            else:
                chunk = silence
            deadline += CHUNK
            delay = deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            # Chunk becomes "available" now (end of its capture window).
            await self.push_audio_frame(
                InputAudioRawFrame(
                    audio=chunk.tobytes(), sample_rate=SR_IN, num_channels=1
                )
            )
            if current is not None:
                push_times.append(time.monotonic())
                pos += n
                if pos >= len(current[0]):
                    current[1].set_result(push_times)
                    current = None


class PacedOutputTransport(BaseOutputTransport):
    """Pretends to be a sound device: write blocks for the chunk's duration."""

    def __init__(self, params: TransportParams):
        super().__init__(params)
        self._play_until = 0.0

    async def start(self, frame: StartFrame):
        await super().start(frame)
        await self.set_transport_ready(frame)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        now = time.monotonic()
        dur = len(frame.audio) / 2 / frame.sample_rate
        if self._play_until > now:
            await asyncio.sleep(self._play_until - now)
        self._play_until = max(self._play_until, time.monotonic()) + dur
        return True


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def read_wav_int16(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).copy()


def sample_time(push_times: list[float], t_in_wav: float) -> float:
    """Monotonic time at which the sample at t_in_wav became available."""
    idx = min(int(t_in_wav / CHUNK), len(push_times) - 1)
    return push_times[idx]


def first(events: list[Event], name: str, after: float, before: float = float("inf")):
    for e in events:
        if e.name == name and after <= e.t < before:
            return e
    return None


def pct(xs, q):
    return float(np.percentile(np.array(xs), q))


@dataclass
class Harness:
    worker: object
    observer: object
    talker: StubTalker
    tape: TapeInputTransport
    runner: WorkerRunner
    run_task: asyncio.Task


async def start_harness(models, talker: StubTalker) -> Harness:
    tparams = TransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=SR_IN,
        audio_out_enabled=True,
        audio_out_sample_rate=24000,
    )
    tape = TapeInputTransport(tparams)
    out = PacedOutputTransport(tparams)
    worker, observer, talker = build_worker(tape, out, models, talker=talker)
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def _started(_w, _f):
        started.set()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    run_task = asyncio.create_task(runner.run())
    await asyncio.wait_for(started.wait(), 30)
    await asyncio.sleep(1.0)  # let VAD/turn analyser see some silence
    return Harness(worker, observer, talker, tape, runner, run_task)


async def stop_harness(h: Harness):
    await h.worker.cancel()
    try:
        await asyncio.wait_for(h.run_task, 10)
    except Exception:
        h.run_task.cancel()


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


async def run_latency(models, meta: list[dict]) -> dict:
    h = await start_harness(models, StubTalker())
    rows = []
    for m in meta:
        pcm = read_wav_int16(OUT / m["file"])
        push_times = await h.tape.play(pcm)
        t_end = sample_time(push_times, m["speech_offset"])
        t_on = sample_time(push_times, m["speech_onset"])
        # wait for the response to be "played"
        await asyncio.sleep(4.0)
        ev = h.observer.events
        horizon = t_end + 4.0
        vad_stop = first(ev, "VADUserStoppedSpeakingFrame", t_end - 0.1, horizon)
        turn_stop_ev = first(ev, "UserStoppedSpeakingFrame", t_end - 0.1, horizon)
        segs = [
            e
            for e in ev
            if e.name == "transcription"
            and t_on <= e.t <= (turn_stop_ev.t if turn_stop_ev else horizon)
        ]
        trans = segs[-1] if segs else None
        n_turns = sum(
            1
            for (tt, n, d) in h.talker.events
            if n == "talker_context" and t_on <= tt < horizon
        )
        ctx_text = next(
            (
                d["text"]
                for (tt, n, d) in h.talker.events
                if n == "talker_context" and t_end - 0.1 <= tt < horizon
            ),
            None,
        )
        st = first(ev, "smart_turn", t_end - 0.1, horizon)
        turn_stop = first(ev, "UserStoppedSpeakingFrame", t_end - 0.1, horizon)
        ctx = first(ev, "LLMContextFrame", t_end - 0.1, horizon)
        llm = first(ev, "llm_text", t_end - 0.1, horizon)
        tts = first(ev, "tts_audio", t_end - 0.1, horizon)
        out = first(ev, "out_audio", t_end - 0.1, horizon)
        if not out:
            logger.error(f"no output for {m['file']}")
            continue
        res = (
            {
                "words": [w for e in segs for w in e.data["result"]["words"]],
                "stt_secs": trans.data["result"]["stt_secs"],
            }
            if trans
            else {}
        )
        # word timestamp error inside the live pipeline (monotonic clock)
        werr = []
        if res:
            t_wav0 = push_times[0] - CHUNK  # monotonic time of WAV sample 0
            from timestamps_test import match

            for p, g in match(res["words"], m["words"]):
                werr.append((p["start"] - t_wav0) - g["start"])
        row = {
            "file": m["file"],
            "text": ctx_text,
            "vad_segments": len(segs),
            "turns": n_turns,
            "smart_turn_all": [
                (round(e.t - t_end, 2), e.data["complete"], round(e.data["prob"], 3))
                for e in ev
                if e.name == "smart_turn" and t_on <= e.t < horizon
            ],
            "segments": [
                e.data["text"]
                for e in ev
                if e.name == "transcription" and t_on <= e.t < horizon
            ],
            "vad_stop": (vad_stop.t - t_end) if vad_stop else None,
            "stt_done": (trans.t - t_end) if trans else None,
            "stt_infer": res.get("stt_secs"),
            "smart_turn_at": (st.t - t_end) if st else None,
            "smart_turn_ms": st.data["ms"] if st else None,
            "smart_turn_prob": st.data["prob"] if st else None,
            "turn_stop": (turn_stop.t - t_end) if turn_stop else None,
            "llm_ctx": (ctx.t - t_end) if ctx else None,
            "llm_first": (llm.t - t_end) if llm else None,
            "tts_first": (tts.t - t_end) if tts else None,
            "tts_ttfa": (tts.t - llm.t) if (tts and llm) else None,
            "out_first": out.t - t_end,
            "word_err_ms": float(np.median(np.abs(werr)) * 1000) if werr else None,
        }
        rows.append(row)
        if row["turns"] > 1 or len(row["smart_turn_all"]) > 1:
            print(
                f"   smart_turn verdicts (t rel. speech end, complete, p): {row['smart_turn_all']} segments={row['segments']}"
            )
        print(
            f"{m['file']}: e2e {row['out_first'] * 1000:6.0f} ms | vad_stop {row['vad_stop'] * 1000:4.0f} "
            f"| stt_done {row['stt_done'] * 1000:4.0f} (infer {row['stt_infer'] * 1000:3.0f}) "
            f"| smart_turn {row['smart_turn_at'] * 1000:4.0f} ({row['smart_turn_ms']:.0f} ms, p={row['smart_turn_prob']:.2f}) "
            f"| turn_stop {row['turn_stop'] * 1000:4.0f} | tts_ttfa {row['tts_ttfa'] * 1000:4.0f} "
            f"| segs {row['vad_segments']} turns {row['turns']} | '{row['text']}'"
        )
    await stop_harness(h)

    summary = {}
    for k in [
        "vad_stop",
        "stt_done",
        "stt_infer",
        "smart_turn_at",
        "smart_turn_ms",
        "turn_stop",
        "llm_first",
        "tts_ttfa",
        "tts_first",
        "out_first",
        "word_err_ms",
    ]:
        xs = [r[k] for r in rows if r[k] is not None]
        if not xs:
            continue
        scale = 1 if k.endswith("_ms") else 1000
        summary[k] = {
            "median": pct(xs, 50) * scale,
            "p90": pct(xs, 90) * scale,
            "max": max(xs) * scale,
        }
    print("\n=== Latency (ms, relative to end of speech; *_ms/infer are durations) ===")
    for k, v in summary.items():
        print(
            f"  {k:14s} median {v['median']:6.0f}  p90 {v['p90']:6.0f}  max {v['max']:6.0f}"
        )
    return {"rows": rows, "summary": summary}


async def run_interrupt(models, meta: list[dict]) -> dict:
    talker = StubTalker(word_delay=0.12, extra_sentences=6)
    h = await start_harness(models, talker)
    a = read_wav_int16(OUT / meta[0]["file"])
    b_meta = meta[1]
    b = read_wav_int16(OUT / b_meta["file"])

    pa = await h.tape.play(a)
    t_a_end = sample_time(pa, meta[0]["speech_offset"])
    # wait until bot audio has been playing ~2 s
    while not first(h.observer.events, "out_audio", t_a_end):
        await asyncio.sleep(0.05)
    t_first_out = first(h.observer.events, "out_audio", t_a_end).t
    await asyncio.sleep(max(0, t_first_out + 2.0 - time.monotonic()))
    pb = await h.tape.play(b)
    t_b_on = sample_time(pb, b_meta["speech_onset"])
    t_b_end = sample_time(pb, b_meta["speech_offset"])
    await asyncio.sleep(4.0)
    await stop_harness(h)

    ev = h.observer.events
    tk = [(t, n, d) for (t, n, d) in talker.events]
    vad_start = first(ev, "VADUserStartedSpeakingFrame", t_b_on - 0.5)
    intr = first(ev, "InterruptionFrame", t_b_on - 0.5)
    cancelled = next(
        (t for t, n, d in tk if n == "talker_cancelled" and t >= t_b_on - 0.5), None
    )
    inside = next(
        (
            t
            for t, n, d in tk
            if n == "talker_task_cancelled_inside" and t >= t_b_on - 0.5
        ),
        None,
    )
    # last bot audio written before the user's second turn produced a reply
    b_reply = first(ev, "llm_text", t_b_end - 0.1)
    outs = [
        e.t
        for e in ev
        if e.name == "out_audio"
        and t_b_on - 1 <= e.t < (b_reply.t if b_reply else 1e18)
    ]
    last_out = max(outs) if outs else None
    talker_done_a = any(n == "talker_done" and t < t_b_on + 5 for t, n, d in tk[:3])
    b_out = first(ev, "out_audio", t_b_end - 0.1)
    texts = [e.data["text"] for e in ev if e.name == "transcription"]
    res = {
        "bot_audio_before_interrupt_s": t_b_on - t_first_out,
        "vad_start_after_onset_ms": (vad_start.t - t_b_on) * 1000
        if vad_start
        else None,
        "interruption_after_onset_ms": (intr.t - t_b_on) * 1000 if intr else None,
        "talker_task_cancelled_after_onset_ms": (cancelled - t_b_on) * 1000
        if cancelled
        else None,
        "cancelled_error_seen_inside_task": inside is not None,
        "last_bot_audio_after_onset_ms": (last_out - t_b_on) * 1000
        if last_out
        else None,
        "first_response_completed_before_interrupt": talker_done_a,
        "second_turn_e2e_ms": (b_out.t - t_b_end) * 1000 if b_out else None,
        "transcripts": texts,
        "talker_events": [(round(t - t_a_end, 3), n, d) for t, n, d in tk],
    }
    print("\n=== Interruption ===")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return res


async def main_async(args):
    meta = json.loads((OUT / "utterances.json").read_text())
    rss0 = rss_mb()
    t = time.perf_counter()
    models = await load_models()
    total_load = time.perf_counter() - t
    import mlx.core as mx

    mem = {
        "rss_before_mb": rss0,
        "rss_after_load_mb": rss_mb(),
        "footprint_after_load_mb": footprint_mb(),
        "mlx_active_mb": mx.get_active_memory() / 2**20,
        "mlx_peak_mb": mx.get_peak_memory() / 2**20,
        "load_secs": {**models.load_secs, "total": total_load},
    }
    print("=== Model load ===")
    print(json.dumps(mem, indent=2))
    results = {"memory": mem}
    if args.only in (None, "latency"):
        results["latency"] = await run_latency(models, meta)
    if args.only in (None, "interrupt"):
        results["interrupt"] = await run_interrupt(models, meta)
    # Smart Turn + Silero sessions are created per pipeline; measure after runs.
    results["memory"]["rss_after_runs_mb"] = rss_mb()
    results["memory"]["footprint_after_runs_mb"] = footprint_mb()
    results["memory"]["max_rss_mb"] = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
    )
    results["memory"]["mlx_peak_after_runs_mb"] = mx.get_peak_memory() / 2**20
    print("\n=== Memory after runs ===")
    print(
        {k: round(v, 1) for k, v in results["memory"].items() if isinstance(v, float)}
    )
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["latency", "interrupt"])
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if args.debug else "WARNING")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
