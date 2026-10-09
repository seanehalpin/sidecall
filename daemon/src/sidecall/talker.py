"""Talker: the read-only conversational Claude Code session per repo (SPEC §6.3)."""

from __future__ import annotations

import contextlib
import json
import logging
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

from .prompts import TALKER_SYSTEM_PROMPT, brief_tool_schema
from .protocol.models import Brief, Task
from .session import OnDelta

if TYPE_CHECKING:
    from .session import RepoSession

log = logging.getLogger(__name__)

SERVER_NAME = "sidecall"
READ_TOOLS = ["Read", "Grep", "Glob"]
TOOL_NAMES = ["dispatch_task", "list_tasks", "get_task_status", "cancel_task", "request_undo"]
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
        task = session.dispatch(brief)
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

    return [dispatch_task, list_tasks, get_task_status, cancel_task, request_undo]


def build_mcp_server(session: RepoSession) -> dict[str, Any]:
    server: McpSdkServerConfig = create_sdk_mcp_server(
        name=SERVER_NAME, version="0.1.0", tools=build_tools(session)
    )
    config: dict[str, Any] = dict(server)
    # Extra keys (except `instance`) are forwarded to the CLI. Without alwaysLoad the
    # tools sit behind ToolSearch, which costs 2-3 s per first use (spike 5).
    config["alwaysLoad"] = True
    return config


def stream_text(event: dict[str, Any], previous: str) -> str:
    """Text to stream for one raw API stream event ("" for none). `previous` is the last
    chunk streamed this turn, used to space out text blocks around tool calls."""
    etype = event.get("type")
    if etype == "content_block_start":
        block = cast(dict[str, Any], event.get("content_block") or {})
        if block.get("type") == "text" and previous and not previous[-1:].isspace():
            return " "
    elif etype == "content_block_delta":
        delta = cast(dict[str, Any], event.get("delta") or {})
        if delta.get("type") == "text_delta":
            return str(delta.get("text") or "")
    return ""


class ClaudeTalker:
    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self._client: ClaudeSDKClient | None = None
        self._in_turn = False

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
        if self._client is None:
            client = ClaudeSDKClient(options=self.options())
            await client.connect()
            self._client = client
        return self._client

    async def reply(self, prompt: str, on_delta: OnDelta) -> str:
        client = await self._ensure_client()
        streamed: list[str] = []
        fallback: list[str] = []
        self._in_turn = True

        def emit(text: str) -> None:
            streamed.append(text)
            on_delta(text)

        try:
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, StreamEvent):
                    if msg.parent_tool_use_id is not None:
                        continue
                    text = stream_text(msg.event, streamed[-1] if streamed else "")
                    if text:
                        emit(text)
                elif isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                    fallback.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    self.session.sessions.set_talker_session(msg.session_id)
                    log.info(
                        "talker turn subtype=%s cost=$%s terminal=%s",
                        msg.subtype,
                        msg.total_cost_usd,
                        msg.terminal_reason,
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
        if self._client is not None and self._in_turn:
            await self._client.interrupt()

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
