"""Announcer scheduling, priority and batching (SPEC §6.7), pure."""

from __future__ import annotations

from sidecall.announcer import (
    QUIET_MS,
    Announcement,
    Announcer,
    announcement_due,
    batch_phrase,
    render_announcement,
)


def ev(kind: str, task_id: str, title: str = "", at: float = 0.0, detail: str = "") -> Announcement:
    return Announcement(kind, task_id, title or task_id, detail, at)  # pyright: ignore[reportArgumentType]


def test_priority_blocked_failed_completed_then_time() -> None:
    a = Announcer()
    a.add(ev("completed", "t1", at=1))
    a.add(ev("failed", "t2", at=2))
    a.add(ev("completed", "t3", at=0))
    a.add(ev("blocked", "t4", at=3))
    assert [e.task_id for e in a.pending()] == ["t4", "t2", "t3", "t1"]
    assert [e.task_id for e in a.take()] == ["t4", "t2", "t3", "t1"]
    assert not a and a.take() == []


def test_latest_event_per_task_wins() -> None:
    a = Announcer()
    a.add(ev("blocked", "t1"))
    a.add(ev("completed", "t1"))
    assert [(e.kind, e.task_id) for e in a.pending()] == [("completed", "t1")]


def test_resolve_blocked_and_mark_seen() -> None:
    a = Announcer()
    a.add(ev("blocked", "t1"))
    a.add(ev("completed", "t2"))
    a.add(ev("failed", "t3"))
    a.resolve_blocked("t2")  # not blocked: untouched
    assert len(a.pending()) == 3
    a.mark_seen()  # a turn carried the task notes
    assert [e.task_id for e in a.pending()] == ["t1"]
    a.resolve_blocked("t1")
    assert not a


def test_due_waits_for_quiet_and_idle_agent() -> None:
    now = 10_000.0
    base = {"pending": True, "user_speaking": False, "agent_busy": False}
    assert announcement_due(now, last_user_activity=None, **base)
    assert announcement_due(now, last_user_activity=now - QUIET_MS, **base)
    assert not announcement_due(now, last_user_activity=now - QUIET_MS + 1, **base)
    assert not announcement_due(
        now, pending=False, user_speaking=False, agent_busy=False, last_user_activity=None
    )
    assert not announcement_due(
        now, pending=True, user_speaking=True, agent_busy=False, last_user_activity=None
    )
    assert not announcement_due(
        now, pending=True, user_speaking=False, agent_busy=True, last_user_activity=None
    )


def test_batch_phrases() -> None:
    assert batch_phrase([]) == ""
    assert batch_phrase([ev("completed", "t", "Card spacing")]) == "Card spacing is done."
    assert batch_phrase([ev("failed", "t", "Red heading")]) == "Red heading failed."
    assert batch_phrase([ev("blocked", "t", "Add zod")]) == "Add zod needs your OK."
    two_done_one_ok = [ev("blocked", "a"), ev("completed", "b"), ev("completed", "c")]
    assert batch_phrase(two_done_one_ok) == "One needs your OK and two things finished."
    mixed = [ev("blocked", "a"), ev("blocked", "b"), ev("failed", "c"), ev("completed", "d")]
    assert batch_phrase(mixed) == "Two need your OK, one failed and one thing finished."
    assert batch_phrase([ev("failed", "a"), ev("failed", "b")]) == "Two things failed."
    many = [ev("completed", str(i)) for i in range(12)]
    assert batch_phrase(many) == "12 things finished."


def test_render_announcement_uses_notes_and_adds_missing_events() -> None:
    events = [
        ev("blocked", "t1", "Add zod", detail="package install: pnpm add zod"),
        ev("completed", "t2", "Card spacing"),
        ev("completed", "t3", "Red heading"),
    ]
    notes = ["[System] Task 'Card spacing' completed: tightened padding"]
    prompt = render_announcement(events, notes)
    lines = prompt.splitlines()
    assert lines[0] == notes[0]
    assert "waiting for the user's OK" in prompt and "pnpm add zod" in prompt
    assert prompt.count("Card spacing") == 1  # the note already covers it
    assert "[System] Task 'Red heading' completed" in prompt
    assert "has paused" in lines[-1]
    assert "One needs your OK and two things finished." in lines[-1]
    assert "User:" not in prompt
    assert "just started a voice call" in render_announcement(events, opening=True)
