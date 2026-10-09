"""Automated check of the live server path without a browser or mic.

An aiortc peer streams out/utt_00.wav (then silence) as its "mic", receives the
bot's audio track and data-channel messages, and measures end-of-speech ->
first non-silent bot audio received over WebRTC (includes Opus + jitter buffer).

    uv run python server.py &            # wait for "models ready"
    uv run python webrtc_client_test.py [--files utt_00.wav utt_05.wav]
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import json
import time
import wave
from pathlib import Path

import aiohttp
import numpy as np
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

OUT = Path(__file__).parent / "out"
SR = 48000
PTIME = 0.02


class WavTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self):
        super().__init__()
        self.queue: list[np.ndarray] = []
        self.pos = 0
        self.cur: np.ndarray | None = None
        self.pts = 0
        self.t0: float | None = None
        self.started_at: float | None = None  # monotonic when current clip started

    def play(self, pcm48: np.ndarray):
        self.queue.append(pcm48)

    async def recv(self):
        n = int(SR * PTIME)
        if self.t0 is None:
            self.t0 = time.monotonic()
        target = self.t0 + self.pts / SR
        d = target - time.monotonic()
        if d > 0:
            await asyncio.sleep(d)
        if self.cur is None and self.queue:
            self.cur, self.pos = self.queue.pop(0), 0
            self.started_at = time.monotonic()
        if self.cur is not None:
            chunk = self.cur[self.pos : self.pos + n]
            self.pos += n
            if self.pos >= len(self.cur):
                self.cur = None
            if len(chunk) < n:
                chunk = np.concatenate([chunk, np.zeros(n - len(chunk), np.int16)])
        else:
            chunk = np.zeros(n, np.int16)
        frame = AudioFrame.from_ndarray(
            chunk.reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = SR
        frame.pts = self.pts
        frame.time_base = fractions.Fraction(1, SR)
        self.pts += n
        return frame


def load48(name: str) -> np.ndarray:
    import soxr

    with wave.open(str(OUT / name)) as w:
        x = (
            np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
            / 32768
        )
    return (soxr.resample(x, 16000, SR) * 32767).astype(np.int16)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--files", nargs="*", default=["utt_00.wav", "utt_05.wav", "utt_01.wav"]
    )
    ap.add_argument("--url", default="http://localhost:7860/api/offer")
    args = ap.parse_args()
    meta = {m["file"]: m for m in json.loads((OUT / "utterances.json").read_text())}

    pc = RTCPeerConnection()
    track = WavTrack()
    pc.addTrack(track)
    dc = pc.createDataChannel("app")
    msgs: list[tuple[float, dict]] = []
    loud: list[float] = []  # monotonic times of received non-silent audio frames

    @dc.on("message")
    def _on_msg(m):
        try:
            d = json.loads(m)
        except Exception:
            return
        if d.get("type") != "signalling":
            msgs.append((time.monotonic(), d))

    @pc.on("track")
    def _on_track(t):
        async def reader():
            while True:
                try:
                    f = await t.recv()
                except Exception:
                    return
                a = f.to_ndarray().astype(np.float32) / 32768
                if np.sqrt(np.mean(a**2)) > 0.01:
                    loud.append(time.monotonic())

        asyncio.ensure_future(reader())

    await pc.setLocalDescription(await pc.createOffer())
    async with aiohttp.ClientSession() as s:
        async with s.post(
            args.url,
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        ) as r:
            ans = await r.json()
    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=ans["sdp"], type=ans["type"])
    )
    await asyncio.sleep(2.0)

    results = []
    for name in args.files:
        m = meta[name]
        track.play(load48(name))
        while track.started_at is None or track.cur is not None or track.queue:
            await asyncio.sleep(0.02)
        t_end = track.started_at + m["speech_offset"]
        track.started_at = None
        await asyncio.sleep(4.0)
        first_loud = next((t for t in loud if t > t_end + 0.05), None)
        lat = next(
            (d for t, d in msgs if d.get("type") == "latency" and t > t_end), None
        )
        results.append((name, first_loud and (first_loud - t_end) * 1000, lat))
        print(
            f"{name}: received bot audio {((first_loud - t_end) * 1000) if first_loud else float('nan'):.0f} ms after end of speech "
            f"(client-side, incl. Opus/jitter); server-reported e2e {lat and lat['e2e_ms']} ms"
        )
    print("\ndata channel messages:")
    for t, d in msgs:
        print("  ", json.dumps(d)[:160])
    await pc.close()


if __name__ == "__main__":
    asyncio.run(main())
