"""TalkerProcessor: the pipeline's bridge to a `TalkerFn` (SPEC §6.8).

On each `LLMContextFrame` (a finished user turn) it streams the talker's reply as
`LLMTextFrame`s from a background task. On `InterruptionFrame` it cancels that task.
Pipecat doesn't cancel tasks a processor started itself, and after a cancel it must not
push `LLMFullResponseEndFrame`, or TTS flushes a half sentence.

Phase 2: the real talker also needs `ClaudeSDKClient.interrupt()` on cancel; pass it
as `on_interrupt`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .call import CallEvents, TalkerFn


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


class TalkerProcessor(FrameProcessor):
    def __init__(
        self,
        talker: TalkerFn,
        events: CallEvents,
        *,
        on_interrupt: Callable[[], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)  # pyright: ignore[reportUnknownMemberType]
        self._talker = talker
        self._events = events
        self._on_interrupt = on_interrupt
        self._task: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            if frame.speculation:
                return
            text = last_user_text(frame.context).strip()
            await self._cancel()
            if text:
                self._events.user_turn(text)
                self._task = self.create_task(self._respond(text), "talker_respond")  # pyright: ignore[reportUnknownMemberType]
        elif isinstance(frame, InterruptionFrame):
            if await self._cancel():
                self._events.interrupted()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        await self._cancel()
        await super().cleanup()

    async def _cancel(self) -> bool:
        task, self._task = self._task, None
        if task is None or task.done():
            return False
        await self.cancel_task(task)  # pyright: ignore[reportUnknownMemberType]
        if self._on_interrupt is not None:
            await self._on_interrupt()
        self._events.agent_done(interrupted=True)
        return True

    async def _respond(self, text: str) -> None:
        await self.push_frame(LLMFullResponseStartFrame())
        async for chunk in self._talker(text):
            if chunk:
                self._events.agent_delta(chunk)
                await self.push_frame(LLMTextFrame(chunk))
        # Not reached after a cancel (see module docstring).
        await self.push_frame(LLMFullResponseEndFrame())
        self._events.agent_done(interrupted=False)
