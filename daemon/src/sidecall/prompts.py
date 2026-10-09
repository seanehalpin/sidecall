"""Prompt text and pure renderers for the talker and the worker (SPEC §6.3-6.5)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .protocol.models import Brief, Pick, Target, Task

TALKER_SYSTEM_PROMPT = """\
You are Sidecall's talker: the conversational half of a tool for prototyping by talking. \
The user is looking at a local web prototype in their browser. Its source code is the \
repository in your working directory.

How you work:
- Use Read, Grep and Glob to understand the code when you need to. Keep it brief.
- You cannot edit files or run commands. A separate background worker makes changes. \
You hand it work by calling dispatch_task with a clear brief.
- Keep replies short and conversational, like speech: one to three short sentences, \
natural contractions, no markdown, no lists, no code blocks. Your replies may be read aloud.
- Markers like [1] or [2] refer to elements the user picked; they are listed after the \
user's text when present. Resolve them against that list.
- If a request is ambiguous, ask exactly one short clarifying question.
- When the request is clear, confirm the brief in one sentence and call dispatch_task \
in the same turn. Don't wait for a second confirmation. Put the files (repo-relative \
paths) and lines you found into the brief's targets.
- Never say a change is done until a system note says the task completed. After \
dispatching, say it's queued or on its way, never that it's done.
- Lines starting with [System] are status notes from Sidecall about tasks, not from the user. \
Mention completed or failed tasks briefly when relevant.
- Use list_tasks, get_task_status, cancel_task and request_undo when the user asks about, \
stops, or wants to undo work.
"""

WORKER_SYSTEM_APPEND = """\
You are running as Sidecall's background worker. Tasks arrive as briefs written by \
another agent who talked with the user. The user is watching the result live in their \
browser; their dev server hot-reloads on save.
Rules:
- Make the requested change and keep it minimal. Don't refactor unrelated code.
- Never run git push. Don't commit unless the brief asks for it.
- Don't start, restart or stop dev servers.
- If a step is denied, continue without it if you can, and mention it in your summary.
- Finish with a one to three sentence plain-text summary of what you changed.
"""

UNDO_BRIEF = Brief(
    title="Undo last change",
    goal="Undo the most recent change you made in this session, restoring the files to "
    "how they were before it.",
    targets=[Target(description="The files touched by your previous task")],
    changes=["Revert the edits from your previous task only"],
    acceptance_criteria=["The previous task's edits are reverted", "No other changes"],
    out_of_scope=["Earlier tasks", "Any git history rewriting"],
)


# --- worker ------------------------------------------------------------------------


def render_target(target: Target) -> str:
    parts: list[str] = []
    if target.pick_id is not None:
        parts.append(f"[{target.pick_id}]")
    if target.file:
        parts.append(f"{target.file}:{target.line}" if target.line is not None else target.file)
    if target.component:
        parts.append(f"<{target.component}>")
    location = " ".join(parts)
    return f"{location} — {target.description}" if location else target.description


def render_pick_summary(pick: Pick) -> str:
    """One-line pick summary (SPEC §6.9 example format)."""
    parts: list[str] = []
    if pick.element is not None:
        parts.append(pick.element.selector)
    elif pick.region is not None:
        parts.append(f"region with {len(pick.region.elements)} elements")
    chain = [c.name for c in pick.framework.component_chain if c.name]
    if chain:
        parts.append(chain[0])
    src = pick.framework.source
    if src is not None:
        parts.append(f"{src.file}:{src.line}:{src.column} ({src.precision})")
    if pick.repeat is not None:
        parts.append(f"repeated {pick.repeat.count}x, instance {pick.repeat.index}")
    if pick.element is not None and pick.element.computed_styles:
        styles = "; ".join(f"{k} {v}" for k, v in list(pick.element.computed_styles.items())[:6])
        parts.append(styles)
    return f"[{pick.id}] " + " — ".join(parts)


def _section(title: str, items: Sequence[str]) -> list[str]:
    if not items:
        return []
    return [f"{title}:", *(f"- {item}" for item in items), ""]


def render_worker_prompt(task: Task, picks: Sequence[Pick] = ()) -> str:
    brief = task.brief
    lines: list[str] = [f"# Task: {brief.title}", "", f"Goal: {brief.goal}", ""]
    lines += _section("Targets", [render_target(t) for t in brief.targets])
    lines += _section("Changes", brief.changes)
    lines += _section("Acceptance criteria", brief.acceptance_criteria)
    lines += _section("Out of scope", brief.out_of_scope)
    if brief.notes:
        lines += ["Notes:", brief.notes, ""]
    if picks:
        lines += _section("Picked elements", [render_pick_summary(p) for p in picks])
    lines += [
        "Instructions:",
        "- Make the change described above. Keep it minimal and focused on the brief.",
        "- Never run git push.",
        "- Don't start dev servers; the user's dev server is already running and hot-reloads.",
        "- When done, reply with a 1-3 sentence plain-text summary of what changed.",
    ]
    return "\n".join(lines)


# --- talker ------------------------------------------------------------------------


def render_talker_turn(text: str, notes: Sequence[str] = (), picks: Sequence[Pick] = ()) -> str:
    """One user turn for the talker: unseen system notes, the user's text, picks."""
    lines = [*notes, f"User: {text}"]
    if picks:
        lines += ["Picks:", *(render_pick_summary(p) for p in picks)]
    return "\n".join(lines)


def task_note(task: Task) -> str:
    """System note telling the talker how a task ended."""
    if task.status == "completed":
        files = f" (files: {', '.join(task.files)})" if task.files else ""
        return f"[System] Task '{task.title}' completed: {task.summary or 'done'}{files}"
    if task.status == "failed":
        return f"[System] Task '{task.title}' failed: {task.error or 'unknown error'}"
    if task.status == "cancelled":
        return f"[System] Task '{task.title}' was cancelled."
    return f"[System] Task '{task.title}' is {task.status}."


def brief_tool_schema() -> dict[str, Any]:
    """dispatch_task input schema: {brief: Brief}, with $defs hoisted to the root
    (nested $defs break the CLI's validation, spike 5)."""
    brief_schema = Brief.model_json_schema(mode="validation", by_alias=False)
    defs = brief_schema.pop("$defs", {})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"brief": brief_schema},
        "required": ["brief"],
    }
    if defs:
        schema["$defs"] = defs
    return schema
