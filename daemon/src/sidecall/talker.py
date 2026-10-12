"""Talker: the read-only conversational Claude Code session per repo (SPEC §6.3)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpSdkServerConfig,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)
from pydantic import ValidationError

from .prompts import TALKER_SYSTEM_PROMPT, brief_tool_schema, pick_payload_json
from .protocol.models import Brief, Task
from .session import OnDelta
from .uploads import Image, LabelledImage, sdk_prompt

if TYPE_CHECKING:
    from .session import RepoSession

log = logging.getLogger(__name__)

SERVER_NAME = "sidecall"
READ_TOOLS = ["Read", "Grep", "Glob"]
TOOL_NAMES = [
    "dispatch_task",
    "list_tasks",
    "get_task_status",
    "cancel_task",
    "request_undo",
    "get_pick",
]
MCP_TOOL_NAMES = [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]
DISALLOWED_TOOLS = ["Edit", "Write", "Bash", "NotebookEdit"]


def _text(value: str, *, error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": value}]}
    if error:
        result["is_error"] = True
    return result


def task_summary(task: Task) -> dict[str, Any]:
    out: dict[str, Any] = {"id": task.id, "title": task.title, "status": task.status}
    if task.last_event and task.status in ("running", "blocked"):
        out["lastEvent"] = task.last_event
    if task.summary:
        out["summary"] = task.summary
    if task.files:
        out["files"] = task.files
    if task.error:
        out["error"] = task.error
    return out


def build_tools(session: RepoSession) -> list[SdkMcpTool[Any]]:
    """The talker's custom tools, bound to one repo session."""

    @tool(
        "dispatch_task",
        "Queue a coding task for the background worker. Call it once the change is clear. "
        "Returns the task id.",
        brief_tool_schema(),
    )
    async def dispatch_task(args: dict[str, Any]) -> dict[str, Any]:
        try:
            brief = Brief.model_validate(args.get("brief"))
        except ValidationError as exc:
            return _text(f"Invalid brief: {exc}", error=True)
        before = len(session.queue.active)
        task = session.dispatch(brief)
        if len(session.queue.active) == before:
            return _text(
                f"Already queued as task {task.id} ('{task.title}') this turn. "
                "Don't dispatch it again."
            )
        ahead = len(session.queue.active) - 1
        where = "It's next." if ahead <= 0 else f"{ahead} task(s) ahead of it."
        return _text(f"Queued task {task.id} ('{task.title}'). {where}")

    @tool("list_tasks", "List the active and recently finished worker tasks.", {})
    async def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
        tasks = session.queue.active + session.queue.recent[-5:]
        return _text(json.dumps([task_summary(t) for t in tasks]))

    @tool(
        "get_task_status",
        "Get the status of one task by id.",
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    )
    async def get_task_status(args: dict[str, Any]) -> dict[str, Any]:
        task = session.get_task(str(args.get("id", "")))
        if task is None:
            return _text("No task with that id.", error=True)
        return _text(json.dumps(task_summary(task)))

    @tool(
        "cancel_task",
        "Cancel a queued or running task by id.",
        {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    )
    async def cancel_task(args: dict[str, Any]) -> dict[str, Any]:
        ok = await session.cancel(str(args.get("id", "")))
        return _text("Cancelled." if ok else "No active task with that id.", error=not ok)

    @tool(
        "request_undo",
        "Queue a task asking the worker to undo its most recent change.",
        {},
    )
    async def request_undo(args: dict[str, Any]) -> dict[str, Any]:
        task = session.request_undo()
        return _text(f"Queued undo as task {task.id}.")

    @tool(
        "get_pick",
        "Get the full payload of a picked element by its [n] id: outerHTML, attributes, all "
        "computed styles, props, component chain, page info, repeat info and screenshot refs. "
        "Use it when the inline summary isn't enough.",
        {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    )
    async def get_pick(args: dict[str, Any]) -> dict[str, Any]:
        try:
            pick_id = int(args.get("id"))  # pyright: ignore[reportArgumentType]
        except (TypeError, ValueError):
            return _text("id must be an integer.", error=True)
        pick = session.picks.get(pick_id)
        if pick is None:
            return _text("No pick with that id (it may have been cleared).", error=True)
        return _text(pick_payload_json(pick))

    return [dispatch_task, list_tasks, get_task_status, cancel_task, request_undo, get_pick]


def build_mcp_server(session: RepoSession) -> dict[str, Any]:
    server: McpSdkServerConfig = create_sdk_mcp_server(
        name=SERVER_NAME, version="0.1.0", tools=build_tools(session)
    )
    config: dict[str, Any] = dict(server)
    # Extra keys (except `instance`) are forwarded to the CLI. Without alwaysLoad the
    # tools sit behind ToolSearch, which costs 2-3 s per first use (spike 5).
    config["alwaysLoad"] = True
    return config


def _is_text_delta(event: dict[str, Any]) -> bool:
    if event.get("type") != "content_block_delta":
        return False
    delta = cast(dict[str, Any], event.get("delta") or {})
    return delta.get("type") == "text_delta" and bool(delta.get("text"))


def is_dispatch(block: dict[str, Any]) -> bool:
    """A dispatch_task tool call (MCP tools are named mcp__<server>__<tool>)."""
    return str(block.get("name") or "").endswith("dispatch_task")


class NarrationFilter:
    """Turns raw API stream events into text worth showing (and later speaking).

    Text the talker writes right before a Read/Grep/Glob call is narration ("Let me
    check the routes..."), so each assistant message's text is held back until we know
    what follows it: a read tool drops it; anything else (end of turn, or one of
    Sidecall's own tools such as dispatch_task, which usually follows the one-sentence
    brief confirmation) releases it. The cost is that a reply appears a message at a
    time rather than token by token; talker replies are one to three short sentences.
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._shown = False
        # Said something, then dispatched: anything after would repeat the confirmation.
        self._confirmed = False

    def feed(self, event: dict[str, Any]) -> str:
        etype = event.get("type")
        if etype == "content_block_start":
            block = cast(dict[str, Any], event.get("content_block") or {})
            if block.get("type") == "tool_use":
                if block.get("name") in READ_TOOLS:
                    self._buffer.clear()
                    return ""
                out = self._flush()
                if is_dispatch(block) and self._shown:
                    self._confirmed = True
                return out
        elif etype == "content_block_delta":
            delta = cast(dict[str, Any], event.get("delta") or {})
            if delta.get("type") == "text_delta":
                self._buffer.append(str(delta.get("text") or ""))
        elif etype == "message_delta":
            delta = cast(dict[str, Any], event.get("delta") or {})
            reason = delta.get("stop_reason")
            if reason and reason != "tool_use":
                return self._flush()
        elif etype == "message_stop":
            return self._flush()
        return ""

    def _flush(self) -> str:
        text = "".join(self._buffer).strip()
        self._buffer.clear()
        if not text or self._confirmed:
            return ""
        out = f" {text}" if self._shown else text
        self._shown = True
        return out


class SpokenFilter:
    """Voice turns: speak the opening sentence at once, but never the narration.

    Text is held as it streams. The turn's first complete sentence is released as soon as
    it ends (so TTS can start within a few tokens), unless a file read follows it first.
    Anything after that is held until we see what follows: a Read/Grep/Glob call drops it
    as narration ("Now let me find the dropdown:"), while the end of the turn or one of
    Sidecall's own tools (dispatch_task after "On it.") releases it. Released text ends
    with a newline, which tells TTS to speak what it has (`voice.call.ReplyStream`).
    """

    _SENTENCE_END = re.compile(r"[.!?…](?=\s)")

    def __init__(self) -> None:
        self._buffer = ""
        self._opening = True
        self._shown = False
        self._confirmed = False  # as NarrationFilter: one confirmation per hand-over

    def feed(self, event: dict[str, Any]) -> str:
        etype = event.get("type")
        if etype == "content_block_start":
            block = cast(dict[str, Any], event.get("content_block") or {})
            if block.get("type") == "tool_use":
                self._opening = False
                if block.get("name") in READ_TOOLS:
                    self._buffer = ""
                    return ""
                out = self._release(self._buffer)
                if is_dispatch(block) and self._shown:
                    self._confirmed = True
                return out
            return ""
        if etype == "content_block_delta":
            delta = cast(dict[str, Any], event.get("delta") or {})
            if delta.get("type") != "text_delta":
                return ""
            self._buffer += str(delta.get("text") or "")
            if self._opening:
                m = self._SENTENCE_END.search(self._buffer)
                if m:
                    head, self._buffer = self._buffer[: m.end()], self._buffer[m.end() :]
                    self._opening = False
                    return self._release(head, keep_rest=True)
            return ""
        if etype == "message_delta":
            delta = cast(dict[str, Any], event.get("delta") or {})
            reason = delta.get("stop_reason")
            if reason and reason != "tool_use":
                return self._release(self._buffer)
            return ""
        if etype == "message_stop":
            return self._release(self._buffer)
        return ""

    def _release(self, text: str, keep_rest: bool = False) -> str:
        if not keep_rest:
            self._buffer = ""
        text = text.strip()
        if not text or self._confirmed:
            return ""
        out = f" {text}\n" if self._shown else f"{text}\n"
        self._shown = True
        return out


@dataclass
class TurnTiming:
    """Seconds from sending the query (talker latency, SPEC §6.8)."""

    first_delta: float | None = None
    """First text token from the API."""
    first_release: float | None = None
    """First text handed on (to the rail, or to TTS in a spoken turn)."""
    narration_release: float | None = None
    """When NarrationFilter would have released its first text (its cost, for voice)."""
    result: float | None = None
    spoken: bool = False
    cost_usd: float | None = None


class ClaudeTalker:
    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self._client: ClaudeSDKClient | None = None
        self._in_turn = False
        self._abort = False
        self._connect_lock = asyncio.Lock()
        self.timings: list[TurnTiming] = []

    def options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=self.session.repo_path,
            model=self.session.talker_model,
            system_prompt=TALKER_SYSTEM_PROMPT,
            tools=list(READ_TOOLS),
            allowed_tools=[*READ_TOOLS, *MCP_TOOL_NAMES],
            disallowed_tools=list(DISALLOWED_TOOLS),
            mcp_servers={SERVER_NAME: build_mcp_server(self.session)},  # pyright: ignore[reportArgumentType]
            thinking={"type": "disabled"},
            include_partial_messages=True,
            setting_sources=["project"],
            verbatim_prompts=True,
            resume=self.session.sessions.data.talker_session_id,
        )

    async def _ensure_client(self) -> ClaudeSDKClient:
        async with self._connect_lock:
            if self._client is None:
                client = ClaudeSDKClient(options=self.options())
                await client.connect()
                self._client = client
            return self._client

    async def warm(self) -> None:
        """Connect ahead of the first turn (a voice call starting)."""
        await self._ensure_client()

    async def reply(
        self,
        prompt: str,
        on_delta: OnDelta,
        image: Image | None = None,
        extra_images: Sequence[LabelledImage] = (),
        *,
        spoken: bool = False,
    ) -> str:
        self._in_turn = True
        self._abort = False
        streamed: list[str] = []
        fallback: list[str] = []
        narration = NarrationFilter()
        voice = SpokenFilter() if spoken else None
        timing = TurnTiming(spoken=spoken)
        self.timings.append(timing)
        t0 = time.perf_counter()

        def emit(text: str) -> None:
            if timing.first_release is None:
                timing.first_release = time.perf_counter() - t0
            streamed.append(text)
            on_delta(text)

        try:
            client = await self._ensure_client()
            t0 = time.perf_counter()
            await client.query(sdk_prompt(prompt, image, extra_images))
            if self._abort:  # interrupted while connecting or sending
                await client.interrupt()
            async for msg in client.receive_response():
                if isinstance(msg, StreamEvent):
                    if msg.parent_tool_use_id is not None:
                        continue
                    event = msg.event
                    if timing.first_delta is None and _is_text_delta(event):
                        timing.first_delta = time.perf_counter() - t0
                    held = narration.feed(event)
                    if held and timing.narration_release is None:
                        timing.narration_release = time.perf_counter() - t0
                    text = voice.feed(event) if voice is not None else held
                    if text:
                        emit(text)
                elif isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                    fallback.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    timing.result = time.perf_counter() - t0
                    timing.cost_usd = msg.total_cost_usd
                    self.session.sessions.set_talker_session(msg.session_id)
                    log.info(
                        "talker turn subtype=%s cost=$%s terminal=%s first_text=%.2fs%s",
                        msg.subtype,
                        msg.total_cost_usd,
                        msg.terminal_reason,
                        timing.first_release or -1,
                        " (spoken)" if spoken else "",
                    )
        except Exception:
            await self.close()
            raise
        finally:
            self._in_turn = False
        if streamed:
            return "".join(streamed)
        return " ".join(t.strip() for t in fallback if t.strip())

    async def interrupt(self) -> None:
        if not self._in_turn:
            return
        self._abort = True
        if self._client is not None:
            await self._client.interrupt()

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
