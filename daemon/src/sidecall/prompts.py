"""Prompt text and pure renderers for the talker and the worker (SPEC §6.3-6.5)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .protocol.models import Brief, PageContext, Pick, Target, Task

PickRef = Pick | int
"""A pick, or the bare id of one that is no longer available."""

TALKER_SYSTEM_PROMPT = """\
You are Sidecall's talker: the conversational half of a tool for prototyping by talking. \
The user is looking at a local web prototype in their browser. Its source code is the \
repository in your working directory.

How you work:
- Use Read, Grep and Glob to understand the code when you need to. Keep it brief.
- Look silently. Never narrate what you're reading or about to do ("Let me check...", \
"Let me look..."): write nothing until you're ready to answer, then answer once.
- You cannot edit files or run commands. A separate background worker makes changes. \
You hand it work by calling dispatch_task with a clear brief.
- Keep replies short and conversational, like speech: one to three short sentences, \
natural contractions, no markdown, no lists, no code blocks. Your replies may be read aloud.
- Markers like [1] or [2] refer to elements the user picked; they are listed after the \
user's text under "Picks:", with their source file and line, component, repeat info and key \
styles, and a cropped screenshot of each. Resolve every marker against that list. If the \
summary isn't enough, call get_pick(id) for the full payload (HTML, all styles, props).
- Package installs and a few other commands pause for the user's approval in the panel. \
That's normal: dispatch them anyway. If the user denied one earlier and now says to go \
ahead, dispatch it again as a new task (they'll be asked again). Never tell the user to \
run a command themselves.
- Put the picks into the brief's targets with pick_id, file, line and component.
- A pick with "repeated N×" (N > 1) is one instance of a repeated template. Change the shared \
template or component the instances come from, not one copy, unless the user says "just this one".
- If a pick has no source, say in the brief what the worker should search for (the \
component, class names or text). A pick marked "(no longer available)" was cleared; ask the \
user to pick it again if you need it.
- If a request is ambiguous, ask exactly one short clarifying question.
- When the request is clear, confirm the brief in one sentence and call dispatch_task \
in the same turn. Don't wait for a second confirmation. Put the files (repo-relative \
paths) and lines you found into the brief's targets.
- Never say a change is done until a system note says the task completed. After \
dispatching, say it's queued or on its way. Don't use the words "done", "finished" or \
"complete" about work you've only dispatched, not even "Done, queuing it".
- If the user follows up about a task that's still queued or running, don't dispatch \
it again; tell them it's in progress.
- Lines starting with [System] are status notes from Sidecall about tasks, not from the user. \
Mention completed or failed tasks briefly when relevant.
- Use list_tasks, get_task_status, cancel_task and request_undo when the user asks about, \
stops, or wants to undo work.

The page the user is on:
- Most turns say which page the user is looking at (title, URL, route), usually with a \
screenshot of what they see. Use it to work out which route file and which element they \
mean ("the dropdown", "this card"), and prefer changing what's on this page.
- Some turns have no page line (the user isn't on the app's tab). Then work from the code \
and their words alone: never ask the user to show or describe the page, and never wait \
for a screenshot.
- If the change would touch a shared component used in many places (design-system \
components, layouts, global styles) and the user didn't say "everywhere", ask one short \
question first, like "Just on this page, or everywhere?", then dispatch.
- Put the page URL and route, and what the user is looking at, into the brief.
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
- Briefs say which page the user was looking at. Scope the change to that page unless \
the brief says otherwise; don't change shared components, layouts or global styles used \
elsewhere without being asked. The exception is the component or template that renders a \
picked element: when a pick is one of several repeated instances, edit that shared template.
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


STYLE_KEYS = (
    "padding",
    "margin",
    "gap",
    "font-size",
    "font-weight",
    "color",
    "background-color",
    "border-radius",
    "box-shadow",
    "display",
)
MAX_SUMMARY_STYLES = 6
_VALUE_LIMIT = 60
_HTML_LIMIT = 1500
_PROPS_LIMIT = 1000


def _clip(text: str, limit: int) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def _num(value: float) -> str:
    return f"{round(value):d}"


def pick_label(pick: Pick) -> str:
    """tag#id.class1.class2 for an element pick (falls back to the CSS selector)."""
    el = pick.element
    if el is None:
        return "region"
    tag = el.tag.lower() or el.selector
    label = tag
    ident = el.attributes.get("id", "").strip()
    if ident:
        label += f"#{ident}"
    classes = el.attributes.get("class", "").split()
    label += "".join(f".{c}" for c in classes[:2])
    return label


def _summary_styles(styles: dict[str, str]) -> str:
    picked = [(k, styles[k]) for k in STYLE_KEYS if styles.get(k)]
    return "; ".join(f"{k} {_clip(v, _VALUE_LIMIT)}" for k, v in picked[:MAX_SUMMARY_STYLES])


def _source_text(pick: Pick) -> str:
    src = pick.framework.source
    if src is not None:
        return f"{src.file}:{src.line}:{src.column} ({src.precision})"
    return "source unknown"


def _repeat_text(pick: Pick) -> str | None:
    r = pick.repeat
    if r is None or r.count <= 1:
        return None
    return f"repeated {r.count}×, instance {r.index + 1}"


def _region_contents(pick: Pick, top: int = 5) -> str:
    assert pick.region is not None
    items: list[str] = []
    for e in pick.region.elements[:top]:
        name = e.selector.split(">")[-1].strip() or e.tag
        text = _clip(e.text, 30)
        items.append(f'{name} "{text}"' if text else name)
    return ", ".join(items)


def render_pick_summary(pick: Pick) -> str:
    """One-line pick summary (SPEC §6.9 format)."""
    parts: list[str] = []
    if pick.kind == "region" and pick.region is not None:
        r = pick.region.rect
        parts.append(f"region {_num(r.w)}×{_num(r.h)} at ({_num(r.x)},{_num(r.y)})")
        contents = _region_contents(pick)
        if contents:
            parts.append(f"contains: {contents} (top {min(5, len(pick.region.elements))})")
    else:
        parts.append(pick_label(pick))
    chain = [c.name for c in pick.framework.component_chain if c.name]
    if chain:
        parts.append(chain[0])
    if pick.kind == "element" or pick.framework.source is not None:
        parts.append(_source_text(pick))
    repeat = _repeat_text(pick)
    if repeat:
        parts.append(repeat)
    if pick.element is not None:
        styles = _summary_styles(pick.element.computed_styles)
        if styles:
            parts.append(styles)
    return f"[{pick.id}] " + " — ".join(parts)


def render_picks_section(refs: Sequence[PickRef]) -> list[str]:
    """The "Picks:" lines for a talker turn (empty when there are none)."""
    if not refs:
        return []
    lines = ["Picks:"]
    for ref in refs:
        lines.append(
            f"[{ref}] (no longer available)" if isinstance(ref, int) else render_pick_summary(ref)
        )
    return lines


def pick_payload_json(pick: Pick) -> str:
    """The full normalised pick as JSON (the talker's get_pick tool)."""
    return json.dumps(pick.model_dump(mode="json", by_alias=True), ensure_ascii=False)


def render_pick_full(pick: Pick) -> str:
    """A pick in full but compact, for the worker prompt."""
    lines = [f"[{pick.id}] {pick.kind}"]
    page = pick.page
    lines.append(f"  page: {page.url} (route {page.route}, {page.color_scheme} scheme)")
    el = pick.element
    if el is not None:
        lines.append(f"  selector: {el.selector}")
        if el.alt_selectors:
            lines.append(f"  alt selectors: {'; '.join(el.alt_selectors)}")
        lines.append(f"  tag: {el.tag}")
        if el.attributes:
            attrs = " ".join(f'{k}="{_clip(v, 120)}"' for k, v in el.attributes.items())
            lines.append(f"  attributes: {attrs}")
        if el.text:
            lines.append(f"  text: {_clip(el.text, 200)}")
        lines.append(f"  html: {_clip(el.outer_html, _HTML_LIMIT)}")
        if el.computed_styles:
            styles = "; ".join(f"{k}: {v}" for k, v in el.computed_styles.items())
            lines.append(f"  computed styles: {styles}")
    if pick.region is not None:
        r = pick.region.rect
        lines.append(f"  region: {_num(r.w)}×{_num(r.h)} at ({_num(r.x)},{_num(r.y)})")
        for e in pick.region.elements:
            lines.append(f"    - {e.selector} {_clip(e.text, 60)}".rstrip())
    fw = pick.framework
    version = f" {fw.version}" if fw.version else ""
    lines.append(f"  framework: {fw.name}{version}")
    if fw.source is not None:
        lines.append(f"  source: {_source_text(pick)}")
    else:
        lines.append("  source: unknown (search the repo for the component, classes or text)")
    if fw.component_chain:
        chain: list[str] = []
        for c in fw.component_chain:
            where = ""
            if c.file:
                where = f" ({c.file}:{c.line}:{c.column})" if c.line is not None else f" ({c.file})"
            chain.append(f"{c.name}{where}")
        lines.append("  component chain (innermost first; each file is where the tag is written):")
        lines += [f"    - {c}" for c in chain]
    if fw.props is not None:
        props = _clip(json.dumps(fw.props, ensure_ascii=False, default=str), _PROPS_LIMIT)
        lines.append(f"  props: {props}")
    repeat = _repeat_text(pick)
    if repeat:
        lines.append(f"  {repeat} (one of several on the page that share this source)")
    return "\n".join(lines)


def _section(title: str, items: Sequence[str]) -> list[str]:
    if not items:
        return []
    return [f"{title}:", *(f"- {item}" for item in items), ""]


def render_page(page: PageContext) -> str:
    v = page.viewport
    return f'"{page.title}" — {page.url} (route {page.route}, viewport {v.w}x{v.h})'


def render_worker_prompt(
    task: Task,
    picks: Sequence[Pick] = (),
    screenshot_attached: bool | None = None,
    pick_images: bool = False,
) -> str:
    """The worker's prompt for one task. `screenshot_attached` defaults to whether the
    task's page has a screenshot ref; pass False when the image couldn't be loaded."""
    brief = task.brief
    lines: list[str] = [f"# Task: {brief.title}", "", f"Goal: {brief.goal}", ""]
    lines += _section("Targets", [render_target(t) for t in brief.targets])
    lines += _section("Changes", brief.changes)
    lines += _section("Acceptance criteria", brief.acceptance_criteria)
    lines += _section("Out of scope", brief.out_of_scope)
    if brief.notes:
        lines += ["Notes:", brief.notes, ""]
    if picks:
        lines += ["Picked elements (the [n] markers in the brief refer to these):"]
        lines += [render_pick_full(p) for p in picks]
        lines += [""]
        if pick_images:
            lines += [
                "Screenshots of each picked element follow the page screenshot, labelled "
                '"[n] crop" (the element) and "[n] viewport" (the page, element outlined).',
                "",
            ]
    page = task.page
    if page is not None:
        v = page.viewport
        lines += _section(
            "Page the user was looking at",
            [
                f"URL: {page.url}",
                f"Route: {page.route}",
                f'Title: "{page.title}"',
                f"Viewport: {v.w}x{v.h} CSS px at {v.dpr:g}x",
            ],
        )
        if bool(page.screenshot) if screenshot_attached is None else screenshot_attached:
            lines += ["A screenshot of what the user saw is attached.", ""]
    lines += ["Instructions:"]
    if page is not None:
        lines.append(
            "- Scope the change to the page above unless the brief says otherwise. Don't "
            "change shared components, layouts or global styles used on other pages "
            "unless the brief asks for that."
        )
    if picks:
        lines += [
            "- Start from the picked elements' source locations: open the file, find the "
            "element at that line, and edit there. If a source is unknown, search for the "
            "component name, class names or text shown above.",
            "- A pick marked 'repeated N×' (N > 1) is one of several instances rendered by "
            "the same template. Edit the template/component that renders them all, not one "
            "copy, unless the brief says to change only that instance.",
            "- Don't change unrelated instances, components or pages.",
        ]
    lines += [
        "- Make the change described above. Keep it minimal and focused on the brief.",
        "- Never run git push.",
        "- Don't start dev servers; the user's dev server is already running and hot-reloads.",
        "- When done, reply with a 1-3 sentence plain-text summary of what changed.",
    ]
    return "\n".join(lines)


# --- talker ------------------------------------------------------------------------


def render_talker_turn(
    text: str,
    notes: Sequence[str] = (),
    picks: Sequence[PickRef] = (),
    page: PageContext | None = None,
    screenshot_attached: bool | None = None,
) -> str:
    """One user turn for the talker: unseen system notes, the page, the user's text, picks.
    `screenshot_attached` defaults to whether the page has a screenshot ref."""
    lines = [*notes]
    if page is not None:
        lines.append(f"Page: {render_page(page)}")
        if bool(page.screenshot) if screenshot_attached is None else screenshot_attached:
            lines.append("A screenshot of what the user currently sees is attached.")
    lines.append(f"User: {text}")
    lines += render_picks_section(picks)
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
