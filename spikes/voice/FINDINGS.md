# Spike 6 — Voice loop (Pipecat + Parakeet + Smart Turn v3 + Kokoro)

Machine: Apple M4 Pro, 24 GB, macOS 26. Python 3.12.15 (uv).

## Verdict

The loop works fully on-device. With a stubbed LLM, **end of speech → first bot audio is about 0.5–0.6 s on the server and about 0.8 s at a WebRTC client**. That is well under the 1.5 s target and leaves **about 0.7–0.9 s for the talker's first sentence**. Interruptions reach a custom processor as `InterruptionFrame`, and cancelling an in-flight task works: about 340–360 ms from user speech onset to bot audio stopping.

Three findings affect the spec:

1. **parakeet-mlx streaming word timestamps break once the stream is longer than the context window.** Use segment-level offline transcription instead (see the §12.6 answer below).
2. **Smart Turn v3 ends turns early on mid-sentence pauses of 240–420 ms** when the fragment is syntactically complete. This matters for Sidecall because users pause while picking.
3. **Kokoro (mlx-audio) puts about 320 ms of silence in front of every utterance.** Trimming it saves about 0.3 s of perceived latency.

## Versions

| Package | Version |
|---|---|
| pipecat-ai (extras `webrtc,silero,runner`) | 1.12.0 |
| parakeet-mlx | 0.5.3 (model `mlx-community/parakeet-tdt-0.6b-v3`) |
| mlx-audio (extra `tts`) | 0.5.8 (model `mlx-community/Kokoro-82M-bf16`, voice `af_heart`) |
| mlx / mlx-metal | 0.32.3 |
| aiortc | 1.15.0 |
| onnxruntime (Silero + Smart Turn) | 1.24.4 |
| misaki 0.9.4 + spacy 3.8.16 + en_core_web_sm 3.8.0 | Kokoro G2P |

**Pipecat packaging:**
- `webrtc` pulls in aiortc, and `silero` pulls in onnxruntime (the model is bundled).
- **Smart Turn v3 needs no extra.** `LocalSmartTurnAnalyzerV3` and `smart-turn-v3.2-cpu.onnx` ship in the base package. The `local-smart-turn` extra pulls in torch and coremltools and is only for the older v2/CoreML analyzers.
- The `kokoro` extra is **kokoro-onnx**, not MLX.
- `mlx-whisper` exists (`WhisperSTTServiceMLX`) as the configured fallback.
- There is **no Parakeet or MLX-Kokoro service**, so the spike has thin custom services in `voice_services.py`.

**ffmpeg is not needed.** `BaseParakeet.transcribe(path)` shells out to ffmpeg, so the spike bypasses it: `get_logmel(mx.array(pcm16k), model.preprocessor_config)` → `model.generate(mel)[0]`.

**No torch.** `misaki[en]` pulls in torch through `spacy-curated-transformers`, which is only used for the trf model. Depend instead on `misaki espeakng-loader num2words phonemizer-fork spacy` plus the `en_core_web_sm` wheel URL. Otherwise misaki tries `spacy.cli.download` at runtime, which needs pip, and pip isn't in a uv venv.

## Exact APIs used (verified against installed source)

**Runner (renamed in Pipecat 1.12)**
- `PipelineTask` is now **`pipecat.pipeline.worker.PipelineWorker`**.
- `PipelineRunner` is now **`pipecat.workers.runner.WorkerRunner`**: `await runner.add_workers(worker); await runner.run()`.
- The old names are deprecated aliases.

**VAD and turn detection: configured in the user aggregator, not the transport**
- `LLMContextAggregatorPair(LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)), user_turn_strategies=UserTurnStrategies(stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())])))`.
- The aggregator broadcasts `VADUserStarted/StoppedSpeakingFrame` upstream, so an STT placed before it still sees segment boundaries.
- Pipeline order: `transport.input() → stt → user_agg → talker → tts → transport.output() → assistant_agg`.
- Interruptions are on by default (`VADUserTurnStartStrategy(enable_interruptions=True)`). There is no `allow_interruptions` parameter any more.
- `TurnAnalyzerUserTurnStopStrategy` ends a turn when Smart Turn returns COMPLETE **and** a `TranscriptionFrame(finalized=True)` has arrived. `SegmentedSTTService.push_frame` sets `finalized=True`, so with a segmented local STT the turn ends the moment its transcript lands.

**STT service**
- Subclass `SegmentedSTTService` and override `wants_wav_segments -> False`.
- Implement `run_stt(self, audio: bytes)`, yielding `TranscriptionFrame(text, user_id, ts, result=...)`.
- Pass `settings=STTSettings(model=, language=)` to avoid a "NOT_GIVEN" error log.

**TTS service**
- Subclass `TTSService(push_start_frame=True, push_stop_frames=True, settings=TTSSettings(model, voice, language))`.
- Implement `run_tts(self, text, context_id)`, yielding `TTSAudioRawFrame(audio, sample_rate, num_channels, context_id=)`.
- Text aggregation defaults to SENTENCE.

**Kokoro (mlx-audio)**
- `mlx_audio.tts.utils.load(repo)`.
- `model.generate(text, voice=, speed=, lang_code="a", split_pattern=r"\n+")` yields `GenerationResult(audio: mx.array, sample_rate=24000, ...)`.
- Each segment is synthesised whole; there is no streaming within a segment.
- For ground-truth word timings: `model._get_pipeline("a")(text, voice=...)`, whose `Result.tokens[i].start_ts/end_ts` come from predicted durations.

**Parakeet (parakeet-mlx)**
- `parakeet_mlx.from_pretrained(repo)` returns a `ParakeetTDT`.
- `generate(mel)[0]` returns `AlignedResult.tokens: list[AlignedToken(text, start, duration, end, confidence)]`.
- Tokens are SentencePiece sub-words; a leading space starts a new word (`tokens_to_words()`).
- Time resolution is **80 ms** (`time_ratio`).
- Streaming API: `with model.transcribe_stream(context_size=(L, R)) as s: s.add_audio(mx.array(chunk)); s.result / s.finalized_tokens / s.draft_tokens`.

**SmallWebRTC transport**
- Signalling: `SmallWebRTCRequestHandler().handle_web_request(SmallWebRTCRequest, callback)` on `POST /api/offer`, and `handle_patch_request` on `PATCH /api/offer`.
- Transport: `SmallWebRTCTransport(webrtc_connection=conn, params=TransportParams(audio_in_enabled=True, audio_out_enabled=True, audio_in_sample_rate=16000, audio_out_sample_rate=24000))`.
- Messages: `conn.send_app_message(dict)` sends JSON over the client-created data channel; incoming messages arrive through the `on_app_message` handler.
- RTVI is added automatically unless `PipelineWorker(enable_rtvi=False)`.

**Timing**
- `BaseObserver.on_push_frame(FramePushed(source, destination, frame, direction, timestamp))`.

## Item 1: Live server (server.py + index.html)

Run `uv run python server.py` and open http://localhost:7860. Models load and warm up once at startup (about 3.8 s from cache) and are shared across sessions.

The page has:
- Connect and Disconnect buttons.
- The mic, opened with `echoCancellation/noiseSuppression/autoGainControl`, and remote audio playback.
- A VAD dot showing when you're detected as speaking.
- A log of transcripts with per-word offsets, Smart Turn verdicts, interruptions, bot text, and the server-side end-of-speech → first-bot-audio time.
- A "slow / long replies" checkbox for barge-in tests.

**Automated check without a browser** (`webrtc_client_test.py`). An aiortc peer plays WAVs as its mic over real WebRTC/Opus and receives the bot's audio track. All turns transcribed correctly, and all data-channel messages arrived. Client-side end of speech → first non-silent bot audio over 6 turns: **692, 753, 792, 812, 912, 1151 ms (median about 800 ms)**. The 1151 ms was the session's first turn. The client figure is about 200 ms more than the server-side one (Opus, jitter buffer, RTP).

## Item 2: Headless pipeline latency (harness.py)

The real pipeline is driven by two fake transports:
- **Input:** pushes 20 ms `InputAudioRawFrame`s at real-time pace.
- **Output:** its `write_audio_frame` blocks for each chunk's duration, like a sound device.

The test set is 8 Kokoro utterances across 4 voices. Times are measured from the chunk containing the last speech sample.

| Stage | Median (ms) | p90 (ms) | Note |
|---|---|---|---|
| vad_stop | 222 | 235 | Silero stop_secs=0.2 + detection |
| smart_turn_at | 279 | 292 | Smart Turn verdict (inference 54 ms median) |
| stt_infer | 81 | 134 | Parakeet MLX on a 3–4 s segment |
| stt_done / turn_stop | 312 | 351 | = max(Smart Turn, STT) |
| tts_ttfa | 160 | 261 | Kokoro: first text → first audio (whole first sentence) |
| **out_first** | **475** | **599** | **End of speech → first output audio frame** |

- Across four full runs, the out_first median was 475–592 ms and p90 599–812 ms.
- Kokoro's leading silence is trimmed in these numbers.

**Budget:** 1.5 s minus about 0.6 s (p50) or 0.8 s (p90) leaves **about 0.7–0.9 s for the talker's time to first sentence**. `tts_ttfa` scales with first-sentence length (100–400 ms seen), so the talker should open with a short sentence.

## Item 3: Parakeet word timestamps (SPEC §12.6)

Scripts: `timestamps_test.py` and `stream_long_test.py`. Ground truth is Kokoro's per-word `start_ts`.

**Word-start error (8 utterances, 77 words):**

| Mode | Median error | p90 error | Max | Note |
|---|---|---|---|---|
| Offline (word start) | 88 ms | 272 ms | 455 ms | |
| Offline (word end) | 95 ms | 195 ms | | |
| Streaming | 100 ms | 256 ms | | Short clips only |

- The first word is often placed early, e.g. "Make": truth 0.775 s, offline 0.320 s.
- In the live pipeline, absolute word times had a median error of 120 ms (p90 160 ms).

**Streaming over 34 s of concatenated speech:**
- Offline, whole 34 s: median error 74 ms.
- Streaming with `ctx=(256, 256)`: 0 of 85 words matched.
- Streaming with `ctx=(128, 32)`: 0 of 85 matched, the text was garbled, and the maximum token start was 1.92 s for 33.9 s of audio.

**Answer to §12.6.** Streaming timestamps are computed relative to each feature window. They reset once the stream passes the right-context window and frames start finalising. `AlignedSentence` then re-sorts tokens by these wrong times, which scrambles word order. Streaming text is also worse and slower: about 140 ms per 0.5 s chunk, against about 80 ms for a whole 4 s clip offline.

**Recommendation.** Don't use streaming.
- Transcribe each VAD segment offline when VAD stops (`SegmentedSTTService`). It takes about 80–100 ms and runs in parallel with Smart Turn.
- Convert token times to daemon-clock time from the segment's first-sample time. This is implemented.
- A turn can span several segments, so concatenate their words.
- Accuracy (about 90–120 ms median, under 300 ms p90) is fine for inserting `[n]` after the last word that ended before the pick.
- Treat the first word's start as unreliable; its end is fine.

## Item 4: Interruption in a custom FrameProcessor (TalkerProcessor prototype)

How `StubTalker` (`voice_services.py`) handles it:
- **Starting a reply:** on `LLMContextFrame` (skipped when `frame.speculation` is set), it starts the response with `self.create_task(...)`, so `process_frame` keeps receiving frames. The reply streams as `LLMFullResponseStartFrame` → `LLMTextFrame`s → `LLMFullResponseEndFrame`.
- **Cancelling:** on **`InterruptionFrame`** (a SystemFrame; `StartInterruptionFrame` no longer exists), it calls `await self.cancel_task(task)`, then forwards the frame. This is where the real talker will also await `client.interrupt()`.
- **Frame order at barge-in:** `VADUserStartedSpeakingFrame` → `UserStartedSpeakingFrame` → `InterruptionFrame`, all within the same millisecond.

Gotchas:
- The base `FrameProcessor._start_interruption()` only cancels the processor's own frame-processing task. A reply streamed from a separately created task is **not** cancelled for you.
- Don't push `LLMFullResponseEndFrame` from the cancelled task, or TTS flushes a half-finished sentence after the barge-in.

**Measured** (`harness.py --only interrupt`, slow 0.12 s/word stub, barge-in after about 2.9 s of bot speech), all relative to user speech onset:

| Event | Time |
|---|---|
| Interruption | 341 ms |
| Talker task cancelled | 342 ms |
| Last bot audio written | 342 ms |

That is about 340–360 ms from user speech onset to bot audio stopping. The browser's playout buffer is extra and wasn't measured. The second turn was answered correctly.

## Item 5: Load times and memory

**First download:** Parakeet about 2.5 GB (about 1 min); Kokoro about 17 s.

**Load from cache:**

| Step | Time |
|---|---|
| Parakeet load | 0.61 s |
| Kokoro load | 0.26 s |
| Kokoro warm-up (spaCy, voice, Metal kernels) | 1.9 s |
| Parakeet warm-up | 0.75 s |
| **Total** | **3.8 s** |

**Memory:**
- RSS after load: 1.5–1.6 GB.
- macOS memory footprint: about 2.96 GB after load, 1.9–4.0 GB after runs.
- MLX: 1.55 GB active, 2.55 GB peak.
- RSS isn't reliable on macOS for Metal buffers. **Budget about 3 GB.**

## Other findings and gotchas

**Premature turn ends (important).**
- Kokoro test audio has internal pauses of 240–420 ms.
- Silero (stop_secs 0.2) ends a segment there, and Smart Turn v3 returned COMPLETE with p ≥ 0.91 on 5 of 8 fragments, e.g. "Change the submit." (0.981).
- Each fragment became its own turn: the bot began answering, and the continuation interrupted it.
- Smart Turn is audio-only, and Kokoro's prosody may sound more final than a human's, so this needs manual verification. Even so, Sidecall users will pause mid-sentence while pointing.

**Kokoro leading silence.** About 0.32 s on every generation. It is now trimmed to 30 ms on the first chunk of each sentence.

**MLX threading.**
- All MLX work goes through one `ThreadPoolExecutor(max_workers=1)`. It never runs concurrently on Metal and never blocks the event loop.
- Smart Turn uses its own ONNX thread.
- An in-flight Kokoro sentence can't be aborted mid-segment, which can hold the executor for up to about 0.3 s.

**First turn is slower.** The first turn of each process or session takes STT 235–420 ms and e2e about 0.8–1.15 s, probably from MLX compiling kernels for new input shapes. Warming up with several audio lengths should fix it (not done).

**Segments vs turns.** `SegmentedSTTService` transcribes per VAD segment, not per turn, so the TurnAssembler must merge word lists. It keeps only 1 s of pre-speech audio; the spike computes the segment's first-sample time itself.

**Minor:**
- Pipecat warns when VAD `stop_secs` isn't 0.2.
- The first aiortc connect attempt hit the 60 s ICE timeout. Every run after that connected immediately.

## How to run

```bash
cd spikes/voice
uv sync                                   # models download to ~/.cache/huggingface on first use
uv run python gen_wavs.py                 # 8 test utterances -> out/ (+ ground-truth word times)
uv run python timestamps_test.py          # item 3 (offline vs streaming, short clips)
uv run python stream_long_test.py         # item 3 (streaming over 34 s)
uv run python harness.py                  # items 2, 4, 5 -> tables + out/results.json
uv run python server.py                   # item 1: http://localhost:7860
uv run python webrtc_client_test.py       # (server running) automated WebRTC client check
```

## Manual test steps (live browser)

Start `uv run python server.py`, wait for `models ready`, then open http://localhost:7860 in Chrome.

1. **Connect.** Click **Connect** and allow the mic. *Expected:* "data channel open", "bot ready" and "pc: connected"; the status reads "connected — speak".
2. **First utterance.** Say "Make the card header red and drop the shadow." *Expected:*
   - The VAD dot is green while you speak.
   - "you: …" shows the correct text with word offsets, followed by "smart turn: complete p≈0.9+".
   - You hear "You said: …", and the latency line is under 1000 ms.
3. **Repeatability.** Say five different sentences. *Expected:* accurate transcripts and 500–900 ms server-side; by ear, replies start well within 1.5 s.
4. **Mid-sentence pause.** Say "Make this…", pause about 1 s, then "…bigger". *Expected (known risk):* possibly two turns. Note whether it happens and at what pause length.
5. **Barge-in.** Tick **slow / long replies**, ask something, then say "stop, wait" mid-reply. *Expected:* an "interruption" line appears, bot audio stops within about 0.5 s, and the new utterance is answered.
6. **Echo.** Use speakers, no headphones, with slow/long replies on, and stay silent. *Expected:* the bot doesn't interrupt itself and its own words aren't transcribed.
7. **Word timing.** Say "drop the shadow on … the sidebar" with a pick-like pause. *Expected:* word offsets are monotonic and roughly match when you spoke (within about 0.1–0.3 s).
8. **Disconnect.** Click **Disconnect**. *Expected:* the server logs "client disconnected" with no errors, and reconnecting works.

## Recommended SPEC changes

1. **§6.8 STT:** "Parakeet TDT 0.6B v3 (parakeet-mlx), run offline on each VAD segment (custom `SegmentedSTTService`). Streaming mode is not used: its word timestamps are window-relative and break after the context window (§12.6). Word times are converted to daemon-clock time from the segment's first-sample time."
2. **§6.8 pipeline:** "In Pipecat ≥ 1.x, Silero VAD and Smart Turn v3 are configured on the user context aggregator, not the transport. Smart Turn v3 (ONNX) is bundled in the base package. Use `PipelineWorker`/`WorkerRunner`."
3. **§6.8 latency:** "Measured with a stub LLM: about 0.5–0.6 s server-side and about 0.8 s at the client (median). The talker's time-to-first-sentence budget is ≤ 0.7 s, so it should open with a short first sentence. Trim Kokoro's leading silence."
4. **§6.8 interruptions:** "The TalkerProcessor streams from a background task and cancels it, plus `ClaudeSDKClient.interrupt()`, on `InterruptionFrame`. It must not emit `LLMFullResponseEndFrame` after cancellation. Measured barge-in: about 350 ms."
5. **§6.9 turn continuation (new):** "If the user resumes within ~1.5 s of a turn end and the talker hasn't produced audio yet (or only a few words), merge the continuation into the previous turn and re-submit. During a pick, or within ~1 s after one, don't finalise the turn on Smart Turn alone." The thresholds need tuning against real speech (manual step 4).
6. **§6.9 marker placement:** "Timestamps have 80 ms resolution and about 0.1 s median error. Use word end times, because first-word starts can be about 0.4 s early."
7. **§12.6:** resolved.
8. **Resources:** "The voice stack uses about 3 GB of unified memory and warms up in about 4 s from cache. The first download is about 2.6 GB."
