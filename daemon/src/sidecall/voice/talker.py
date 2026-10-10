"""Pipeline processors that connect Pipecat to the `Call` (SPEC §6.8).

- `SegmentTap` (between STT and the user aggregator): reports VAD starts/stops and each
  STT segment with its word times (daemon-clock ms) in pipeline order, so the turn's
  segments are known before the aggregator ends the turn.
- `TalkerProcessor` (after the user aggregator): reports turn ends, and streams replies
  the `Call` hands it (`speak`) into TTS from a background task, a sentence at a time
  (`AggregatedTextFrame`s, which TTS speaks as they are). It aggregates sentences itself
  so it can flush at a pause in the talker's text: Pipecat's aggregator holds "Sure,
  one sec." until the next character, which only comes after the talker's Read. On
  `InterruptionFrame` it cancels that task; closing the reply stream stops the talker's
  turn (`ClaudeSDKClient.interrupt()`). Pipecat doesn't cancel tasks a processor started
  itself, and after a cancel it must not push `LLMFullResponseEndFrame`, or TTS flushes a
  half sentence.
- `OutputTap` (after the output transport): the output transport forwards text and
  control frames in step with playback, so a sentence's `AggregatedTextFrame` arriving
  here means it starts playing, and `LLMFullResponseEndFrame` means the reply finished.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import Any

from pipecat.frames.frames import (
    AggregatedTextFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.base_text_aggregator import Aggregation
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

from .call import CallEvents, ReplyStream
from .turns import TimedWord, monotonic_to_daemon_ms

FIRST_CLAUSE_MIN_CHARS = 30
"""A reply's first sentence is spoken at its first clause break (comma, dash...) once it
is this long: TTS starts a sentence only when it's complete, and a long opening sentence
cost ~0.5 s in the live test."""
_CLAUSE_END = re.compile(r"[,;:\u2014\u2013]\s*$")


def last_user_text(context: LLMContext) -> str:
    for msg in reversed(context.get_messages()):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content: Any = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[Any] = list(content)  # pyright: ignore[reportUnknownArgumentType]
            texts: list[str] = [
                str(p.get("text", ""))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                for p in parts
                if isinstance(p, dict)
            ]
            return " ".join(texts)
    return ""


def segment_words(result: Any, offset_s: float) -> tuple[list[TimedWord], float | None]:
    """Word times from `ParakeetSTTService`'s `TranscriptionFrame.result` (monotonic
    seconds) as daemon-clock ms, plus the segment's start."""
    if not isinstance(result, dict):
        return [], None
    data: dict[str, Any] = result  # pyright: ignore[reportUnknownVariableType]
    words: list[TimedWord] = []
    raw: list[Any] = list(data.get("words") or [])
    for w in raw:
        text = str(getattr(w, "word", "")).strip()
        if text:
            words.append(
                TimedWord(
                    text,
                    monotonic_to_daemon_ms(float(w.start), offset_s=offset_s),
                    monotonic_to_daemon_ms(float(w.end), offset_s=offset_s),
                )
            )
    start = data.get("segment_start")
    seg_start = monotonic_to_daemon_ms(float(start), offset_s=offset_s) if start else None
    return words, seg_start


class SegmentTap(FrameProcessor):
    def __init__(self, events: CallEvents, **kwargs: Any) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._events = events
        self._offset_s = time.time() - time.monotonic()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            words, start = segment_words(frame.result, self._offset_s)
            self._events.user_segment(frame.text, words, start)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._events.user_speaking()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._events.user_stopped()
        await self.push_frame(frame, direction)


class OutputTap(FrameProcessor):
    def __init__(self, events: CallEvents, **kwargs: Any) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._events = events

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if (
                isinstance(frame, AggregatedTextFrame)
                and not isinstance(frame, TTSTextFrame)
                and frame.will_be_spoken
            ):
                self._events.agent_sentence(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                self._events.agent_playback_done()
        await self.push_frame(frame, direction)


class TalkerProcessor(FrameProcessor):
    def __init__(self, events: CallEvents, **kwargs: Any) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._events = events
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._generation = 0  # bumped by each speak() and interruption
        self._said = False
        self.cancelled_at: list[float] = []
        """`time.monotonic()` of each cancelled reply (interruption latency tests)."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            if frame.speculation:
                return
            self._events.user_turn_ended(last_user_text(frame.context).strip())
        elif isinstance(frame, InterruptionFrame):
            self._generation += 1  # a reply queued by speak() but not started is stale
            await self._cancel()
            self._events.interrupted()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    def speak(self, reply: ReplyStream, *, interrupt: bool = False) -> None:
        """Start streaming `reply` (from the event loop, outside frame processing)."""
        self._generation += 1
        self.create_task(self._start(reply, interrupt, self._generation), "talker_speak")  # pyright: ignore[reportUnknownMemberType]

    async def cleanup(self) -> None:
        await self._cancel()
        await super().cleanup()

    async def _start(self, reply: ReplyStream, interrupt: bool, generation: int) -> None:
        async with self._lock:
            if generation != self._generation:
                await reply.aclose()
                return
            await self._cancel()
            if interrupt:
                # Flush whatever TTS and the output still hold for the old reply.
                await self.push_frame(InterruptionFrame())
            self._task = self.create_task(self._respond(reply), "talker_respond")  # pyright: ignore[reportUnknownMemberType]

    async def _cancel(self) -> bool:
        task, self._task = self._task, None
        if task is None or task.done():
            return False
        await self.cancel_task(task)  # pyright: ignore[reportUnknownMemberType]
        self.cancelled_at.append(time.monotonic())
        return True

    async def _respond(self, reply: ReplyStream) -> None:
        await self.push_frame(LLMFullResponseStartFrame())
        sentences = SimpleTextAggregator()
        self._said = False
        async with contextlib.aclosing(reply):  # closing early interrupts the talker
            async for chunk in reply:
                if not chunk:
                    continue
                self._events.agent_text(chunk)
                parts = chunk.split("\n")
                for i, part in enumerate(parts):
                    async for sentence in sentences.aggregate(part):
                        await self._say(sentence)
                    pause = i < len(parts) - 1  # a newline: the talker's text paused
                    opening = not self._said and first_clause_ready(sentences.text.text)
                    if pause or opening:
                        await self._say(await sentences.flush())
        await self._say(await sentences.flush())
        # Not reached after a cancel (see module docstring).
        await self.push_frame(LLMFullResponseEndFrame())
        self._events.agent_stream_done()

    async def _say(self, sentence: Aggregation | None) -> None:
        if sentence is not None and sentence.text.strip():
            self._said = True
            await self.push_frame(AggregatedTextFrame(sentence.text.strip(), sentence.type))


def first_clause_ready(buffered: str) -> bool:
    """Whether the opening text is long enough and ends at a clause break."""
    return len(buffered.strip()) >= FIRST_CLAUSE_MIN_CHARS and bool(_CLAUSE_END.search(buffered))
