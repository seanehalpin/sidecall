from __future__ import annotations

from pathlib import Path

from helpers import make_task

from sidecall.paths import normalise_brief, rel_path
from sidecall.prompts import (
    brief_tool_schema,
    render_talker_turn,
    render_target,
    render_worker_prompt,
    task_note,
)
from sidecall.protocol.models import Brief, Target
from sidecall.talker import stream_text, task_summary
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


def test_stream_text() -> None:
    delta = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}
    assert stream_text(delta, "") == "Hi"
    start = {"type": "content_block_start", "content_block": {"type": "text", "text": ""}}
    assert stream_text(start, "") == ""
    assert stream_text(start, "queued.") == " "
    assert stream_text(start, "queued. ") == ""
    tool_delta = {"type": "content_block_delta", "delta": {"type": "input_json_delta"}}
    assert stream_text(tool_delta, "x") == ""
    assert stream_text({"type": "message_stop"}, "x") == ""


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
