from __future__ import annotations

from pathlib import Path
from typing import Any

from helpers import make_task

from sidecall.paths import normalise_brief, rel_path
from sidecall.prompts import (
    brief_tool_schema,
    render_talker_turn,
    render_target,
    render_worker_prompt,
    task_note,
)
from sidecall.protocol.models import Brief, PageContext, Target, Viewport
from sidecall.talker import NarrationFilter, task_summary
from sidecall.worker import (
    approval_command,
    describe_tool_use,
    hook_output,
    touched_file,
    truncate,
)


def test_render_worker_prompt_has_every_section() -> None:
    task = make_task("Red heading")
    task.brief = Brief(
        title="Red heading",
        goal="Make the home page heading red",
        targets=[
            Target(file="src/routes/+page.svelte", line=10, description="the h1"),
            Target(pick_id=2, component="Card", description="card title"),
            Target(description="anything else"),
        ],
        changes=["color: red on the h1"],
        acceptance_criteria=["the h1 renders red"],
        out_of_scope=["other headings"],
        notes="Use the existing CSS block.",
    )
    prompt = render_worker_prompt(task)
    assert prompt.startswith("# Task: Red heading")
    for expected in [
        "Goal: Make the home page heading red",
        "- src/routes/+page.svelte:10 — the h1",
        "- [2] <Card> — card title",
        "- anything else",
        "Changes:\n- color: red on the h1",
        "Acceptance criteria:\n- the h1 renders red",
        "Out of scope:\n- other headings",
        "Notes:\nUse the existing CSS block.",
        "Never run git push",
        "Don't start dev servers",
        "1-3 sentence",
    ]:
        assert expected in prompt, expected


def test_render_worker_prompt_omits_empty_sections() -> None:
    prompt = render_worker_prompt(make_task())
    assert "Out of scope" not in prompt
    assert "Notes:" not in prompt


def test_render_target_file_without_line() -> None:
    assert render_target(Target(file="a.css", description="d")) == "a.css — d"


def test_render_talker_turn() -> None:
    assert render_talker_turn("hi") == "User: hi"
    turn = render_talker_turn("and now?", ["[System] Task 'A' completed: did it"])
    assert turn == "[System] Task 'A' completed: did it\nUser: and now?"


def test_task_notes() -> None:
    t = make_task("A")
    t.status, t.summary, t.files = "completed", "Made it red.", ["src/a.svelte"]
    assert task_note(t) == "[System] Task 'A' completed: Made it red. (files: src/a.svelte)"
    t.status, t.error = "failed", "boom"
    assert task_note(t) == "[System] Task 'A' failed: boom"
    t.status = "cancelled"
    assert task_note(t) == "[System] Task 'A' was cancelled."


def test_brief_tool_schema_hoists_defs() -> None:
    schema = brief_tool_schema()
    assert schema["required"] == ["brief"]
    assert "$defs" in schema and "Target" in schema["$defs"]
    brief = schema["properties"]["brief"]
    assert "$defs" not in brief
    assert brief["properties"]["targets"]["items"]["$ref"] == "#/$defs/Target"
    assert "acceptance_criteria" in brief["properties"]  # snake_case for the model


def test_describe_tool_use(tmp_path: Path) -> None:
    repo = tmp_path
    f = str(repo / "src" / "lib" / "Card.svelte")
    assert describe_tool_use("Edit", {"file_path": f}, repo) == (
        "edit",
        "Editing src/lib/Card.svelte",
    )
    assert describe_tool_use("Write", {"file_path": f}, repo)[1] == "Writing src/lib/Card.svelte"
    assert describe_tool_use("Read", {"file_path": f}, repo) == (
        "tool",
        "Reading src/lib/Card.svelte",
    )
    assert describe_tool_use("Bash", {"command": "pnpm test"}, repo) == (
        "tool",
        "Running: pnpm test",
    )
    assert describe_tool_use("Grep", {"pattern": "h1"}, repo) == ("tool", "Searching for h1")
    assert describe_tool_use("Mystery", {}, repo) == ("tool", "Using Mystery")


def test_touched_file_and_rel_path(tmp_path: Path) -> None:
    assert touched_file("Edit", {"file_path": str(tmp_path / "a.css")}, tmp_path) == "a.css"
    assert touched_file("Read", {"file_path": str(tmp_path / "a.css")}, tmp_path) is None
    assert rel_path("/elsewhere/x", tmp_path) == "/elsewhere/x"
    assert rel_path("src/x", tmp_path) == "src/x"


def test_truncate() -> None:
    assert truncate("a\n  b") == "a b"
    long = truncate("x" * 500)
    assert len(long) == 120 and long.endswith("…")


def test_hook_output_and_approval_command() -> None:
    out = hook_output("deny", "git push is never allowed")
    assert out == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "git push is never allowed",
        }
    }
    assert approval_command("Bash", {"command": "pnpm add zod"}) == "pnpm add zod"
    assert approval_command("Write", {"file_path": "/tmp/x"}) == "Write /tmp/x"


def _text(t: str) -> dict[str, Any]:
    return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}


def _tool(name: str) -> dict[str, Any]:
    return {"type": "content_block_start", "content_block": {"type": "tool_use", "name": name}}


def _stop(reason: str) -> dict[str, Any]:
    return {"type": "message_delta", "delta": {"stop_reason": reason}}


def _run(events: list[dict[str, Any]]) -> str:
    f = NarrationFilter()
    return "".join(f.feed(e) for e in events)


def test_narration_before_read_tools_is_dropped() -> None:
    events = [
        _text("Let me check the routes."),
        _tool("Read"),
        _stop("tool_use"),
        {"type": "message_stop"},
        _text("Let me look"),
        _text(" closer."),
        _tool("Grep"),
        _stop("tool_use"),
        {"type": "message_stop"},
        _text("It's the h1 in +page.svelte. "),
        _text("I'll make it red."),
        _stop("end_turn"),
        {"type": "message_stop"},
    ]
    assert _run(events) == "It's the h1 in +page.svelte. I'll make it red."


def test_confirmation_before_dispatch_is_kept() -> None:
    events = [
        _text("I'll make the heading red."),
        _tool("mcp__sidecall__dispatch_task"),
        _stop("tool_use"),
        {"type": "message_stop"},
        _text("It's queued."),
        _stop("end_turn"),
        {"type": "message_stop"},
    ]
    assert _run(events) == "I'll make the heading red. It's queued."


def test_plain_reply_and_tool_input_deltas() -> None:
    json_delta = {"type": "content_block_delta", "delta": {"type": "input_json_delta"}}
    assert _run([_text("Hi there."), json_delta, _stop("end_turn")]) == "Hi there."
    assert _run([{"type": "message_stop"}]) == ""


def test_task_summary() -> None:
    t = make_task("A")
    t.status, t.last_event = "running", "Editing a.css"
    assert task_summary(t) == {
        "id": t.id,
        "title": "A",
        "status": "running",
        "lastEvent": "Editing a.css",
    }


def test_normalise_brief(tmp_path: Path) -> None:
    b = Brief(
        title="t",
        goal="g",
        targets=[
            Target(file=str(tmp_path / "src" / "a.css"), description="x"),
            Target(file="./b.css", description="y"),
            Target(description="z"),
        ],
        changes=[],
        acceptance_criteria=[],
    )
    out = normalise_brief(b, tmp_path)
    assert [t.file for t in out.targets] == ["src/a.css", "b.css", None]
    assert b.targets[0].file == str(tmp_path / "src" / "a.css")  # input untouched


def _page(screenshot: str | None = "0123456789abcdef0123456789abcdef") -> PageContext:
    return PageContext(
        url="http://localhost:5173/settings?tab=a",
        route="/settings?tab=a",
        title="Settings",
        viewport=Viewport(w=1440, h=900, dpr=2),
        screenshot=screenshot,
    )


def test_render_talker_turn_with_page() -> None:
    turn = render_talker_turn("dropdown should be magenta", ["[System] note"], page=_page())
    assert turn.splitlines() == [
        "[System] note",
        'Page: "Settings" — http://localhost:5173/settings?tab=a '
        "(route /settings?tab=a, viewport 1440x900)",
        "A screenshot of what the user currently sees is attached.",
        "User: dropdown should be magenta",
    ]
    no_shot = render_talker_turn("hi", page=_page(None))
    assert "screenshot" not in no_shot and no_shot.endswith("User: hi")
    missing = render_talker_turn("hi", page=_page(), screenshot_attached=False)
    assert "screenshot" not in missing


def test_render_worker_prompt_page_section() -> None:
    task = make_task("Magenta dropdown")
    assert "Page the user was looking at" not in render_worker_prompt(task)
    assert "Scope the change" not in render_worker_prompt(task)
    task.page = _page()
    prompt = render_worker_prompt(task)
    section = prompt.split("Page the user was looking at:\n", 1)[1].split("\n\n", 1)[0]
    assert section.splitlines() == [
        "- URL: http://localhost:5173/settings?tab=a",
        "- Route: /settings?tab=a",
        '- Title: "Settings"',
        "- Viewport: 1440x900 CSS px at 2x",
    ]
    assert "A screenshot of what the user saw is attached." in prompt
    instructions = prompt.split("Instructions:\n", 1)[1]
    assert instructions.startswith("- Scope the change to the page above unless the brief")
    assert "screenshot" not in render_worker_prompt(task, screenshot_attached=False)
    task.page = _page(None)
    assert "screenshot" not in render_worker_prompt(task)
