"""Worker: a persistent Claude Code session per repo that executes briefs (SPEC §6.5)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from .classifier import EDIT_TOOLS, classify
from .costs import CostTracker, parse_usage
from .handoff import rotation_reason
from .paths import rel_path
from .prompts import WORKER_SYSTEM_APPEND, render_worker_prompt
from .protocol.messages import TaskEventKind
from .protocol.models import Preset, Task
from .session import OnEvent, WorkerResult
from .uploads import Image, sdk_prompt

if TYPE_CHECKING:
    from .session import RepoSession

log = logging.getLogger(__name__)

HOOK_MATCHER = "Bash|Edit|Write|MultiEdit|NotebookEdit"
TEXT_SNIPPET = 120


# --- pure helpers -----------------------------------------------------------------


def truncate(text: str, limit: int = TEXT_SNIPPET) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def describe_tool_use(
    name: str, tool_input: dict[str, Any], repo: Path
) -> tuple[TaskEventKind, str]:
    """A short progress line for a tool call."""
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    shown = rel_path(path, repo) if isinstance(path, str) else None
    if name in EDIT_TOOLS:
        verb = "Writing" if name == "Write" else "Editing"
        return "edit", f"{verb} {shown or 'a file'}"
    if name == "Read":
        return "tool", f"Reading {shown or 'a file'}"
    if name == "Bash":
        return "tool", truncate(f"Running: {tool_input.get('command', '')}")
    if name in ("Grep", "Glob"):
        pattern = tool_input.get("pattern", "")
        return "tool", truncate(f"Searching for {pattern}" + (f" in {shown}" if shown else ""))
    if name == "TodoWrite":
        return "tool", "Planning"
    return "tool", truncate(f"Using {name}")


def touched_file(name: str, tool_input: dict[str, Any], repo: Path) -> str | None:
    if name not in EDIT_TOOLS:
        return None
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return rel_path(path, repo) if isinstance(path, str) else None


def hook_output(action: str, reason: str) -> HookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": cast(Any, action),
            "permissionDecisionReason": reason,
        }
    }


def approval_command(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    return f"{tool_name} {path}" if path else tool_name


def denied_message(command: str) -> str:
    return (
        f"The user denied this step: {command}. "
        "Do not try to achieve it another way; continue without it if possible."
    )


# --- worker -----------------------------------------------------------------------


class ClaudeWorker:
    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self.repo = session.repo_path
        self._client: ClaudeSDKClient | None = None
        self._task_id: str | None = None
        self._costs = CostTracker()
        self._fresh = False  # the client started a new session and hasn't been seeded
        self._model: str | None = None  # the model the live client is on
        self._context_tokens: int | None = None

    # The hook and the permission callback both read the live preset, so a settings
    # change applies to the next tool call.
    def _preset(self) -> Preset:
        return self.session.preset

    async def _pre_tool_use(
        self, hook_input: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        data = cast(dict[str, Any], hook_input)
        name = str(data.get("tool_name", ""))
        tool_input = cast(dict[str, Any], data.get("tool_input") or {})
        decision = classify(name, tool_input, self.repo, self._preset())
        log.debug("hook %s -> %s (%s)", name, decision.action, decision.reason)
        # Never wait here: hooks time out after 60 s. "ask" hands off to can_use_tool.
        return hook_output(decision.action, decision.reason)

    async def _can_use_tool(
        self, name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        decision = classify(name, tool_input, self.repo, self._preset())
        if decision.action == "deny":
            return PermissionResultDeny(message=f"Blocked by Sidecall: {decision.reason}.")
        if decision.action == "allow":
            return PermissionResultAllow(updated_input=tool_input)
        task_id = self._task_id
        command = approval_command(name, tool_input)
        if task_id is None:
            return PermissionResultDeny(message=denied_message(command))
        allowed = await self.session.approvals.request(task_id, command, decision.reason)
        if allowed:
            return PermissionResultAllow(updated_input=tool_input)
        return PermissionResultDeny(message=denied_message(command), interrupt=False)

    def options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            cwd=self.repo,
            permission_mode="acceptEdits",
            setting_sources=["project"],
            verbatim_prompts=True,
            model=self.session.worker_model,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": WORKER_SYSTEM_APPEND,
            },
            hooks={"PreToolUse": [HookMatcher(matcher=HOOK_MATCHER, hooks=[self._pre_tool_use])]},
            can_use_tool=self._can_use_tool,
            resume=self.session.sessions.data.worker_session_id,
        )

    async def _ensure_client(self) -> ClaudeSDKClient:
        if self._client is None:
            client = ClaudeSDKClient(options=self.options())
            try:
                await client.connect()
            except Exception:
                if self.session.sessions.data.worker_session_id is None:
                    raise
                # A stale session id can make resume fail: start fresh.
                log.warning("worker resume failed; starting a new session")
                self.session.sessions.reset_worker()
                client = ClaudeSDKClient(options=self.options())
                await client.connect()
            self._client = client
            self._model = self.session.worker_model
            data = self.session.sessions.data
            self._costs.reset(
                resumed=data.worker_session_id is not None, baseline=data.worker_sdk_total
            )
            self._fresh = self.session.sessions.data.worker_session_id is None
        return self._client

    async def _maybe_rotate(self) -> None:
        """Start a new session instead of resuming when resuming stops paying."""
        sessions = self.session.sessions
        if sessions.data.worker_session_id is None:
            return
        reason = rotation_reason(self.session.rotation, sessions.worker_stats(), time.time())
        if reason is None:
            return
        log.info(
            "worker session rotated reason=%s old_session=%s",
            reason,
            sessions.data.worker_session_id,
        )
        await self._drop_client()
        sessions.reset_worker()

    async def run(self, task: Task, on_event: OnEvent) -> WorkerResult:
        self._task_id = task.id
        files: list[str] = []
        try:
            image = self._screenshot(task)
            picks = self.session.task_picks(task)
            extra = self.session.worker_images(picks)
            prompt = render_worker_prompt(
                task, picks, screenshot_attached=image is not None, pick_images=bool(extra)
            )
            await self._maybe_rotate()
            client = await self._ensure_client()
            if self._model != self.session.worker_model:
                # Chosen in the UI since the client connected: switch, keeping the session.
                await client.set_model(self.session.worker_model)
                self._model = self.session.worker_model
                log.info("worker switched to %s", self._model)
            if self._fresh:
                prompt = await asyncio.to_thread(self.session.worker_seed) + prompt
                self._fresh = False
                log.info("worker session seeded from the task log")
            await client.query(sdk_prompt(prompt, image, extra))
            result: ResultMessage | None = None
            self._context_tokens = None
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                    usage = parse_usage(msg.usage)
                    if usage is not None:  # each request's own usage: the context size
                        self._context_tokens = (
                            usage["input"] + usage["cache_read"] + usage["cache_write"]
                        )
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            kind, text = describe_tool_use(block.name, block.input, self.repo)
                            on_event(kind, text)
                            f = touched_file(block.name, block.input, self.repo)
                            if f and f not in files:
                                files.append(f)
                        elif isinstance(block, TextBlock) and block.text.strip():
                            on_event("text", truncate(block.text))
                elif isinstance(msg, ResultMessage):
                    result = msg
        except BaseException:
            log.info(
                "worker turn for task %s ended without a result %s",
                task.id,
                CostTracker.unknown().log_fields(),
            )
            await self._drop_client()
            raise
        finally:
            self._task_id = None
        return self._to_result(result, files)

    def _screenshot(self, task: Task) -> Image | None:
        ref = task.page.screenshot if task.page is not None else None
        if not ref:
            return None
        image = self.session.uploads.load(ref)
        if image is None:
            log.warning("screenshot %s for task %s not found", ref, task.id)
        return image

    def _to_result(self, result: ResultMessage | None, files: list[str]) -> WorkerResult:
        if result is None:
            return WorkerResult("failed", files=files, error="The worker stopped without a result")
        cost = self._costs.record(result.total_cost_usd, result.usage)
        self.session.add_cost("worker", cost.turn_cost)
        self.session.sessions.record_worker_result(
            result.session_id,
            cost.turn_cost,
            self._context_tokens,
            time.time(),
            result.total_cost_usd,
        )
        stats = self.session.sessions.worker_stats()
        log.info(
            "worker result subtype=%s error=%s terminal=%s %s context=%s total_session_cost=$%.4f",
            result.subtype,
            result.is_error,
            result.terminal_reason,
            cost.log_fields(),
            self._context_tokens if self._context_tokens is not None else "unknown",
            stats.session_cost,
        )
        if (result.terminal_reason or "").startswith("aborted"):
            return WorkerResult("cancelled", files=files)
        if result.subtype == "success" and not result.is_error:
            return WorkerResult("completed", summary=(result.result or "").strip(), files=files)
        reason = "; ".join(result.errors or []) or result.result or result.subtype
        return WorkerResult("failed", files=files, error=reason.strip())

    async def interrupt(self) -> None:
        if self._client is not None and self._task_id is not None:
            await self._client.interrupt()

    async def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def close(self) -> None:
        await self._drop_client()
