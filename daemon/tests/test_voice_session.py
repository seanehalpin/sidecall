"""RepoSession's voice side (SPEC §6.7-6.9) with a fake talker: spoken replies share the
typed turns' client, notes and picks; interruptions stop the talker; announcements."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from helpers import make_brief, make_pick
from test_server import FakeTalker, FakeWorker

from sidecall.protocol.messages import ServerMessage
from sidecall.protocol.models import PageContext, Viewport
from sidecall.session import RepoSession
from sidecall.voice.turns import PickTime


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[RepoSession]:
    repo = tmp_path / "app"
    (repo / "src" / "lib").mkdir(parents=True)
    (repo / "src" / "lib" / "Card.svelte").write_text("<div></div>\n")
    FakeTalker.instances.clear()
    s = RepoSession(repo, tmp_path / "home", lambda _m: None, FakeTalker, FakeWorker)
    s.start()
    yield s
    await s.close()


def capture(session: RepoSession) -> list[ServerMessage]:
    out: list[ServerMessage] = []
    session._emit = out.append  # pyright: ignore[reportPrivateUsage]
    session.approvals._emit = out.append  # pyright: ignore[reportPrivateUsage]
    return out


async def collect(stream: AsyncIterator[str]) -> str:
    return "".join([c async for c in stream])


class StubCall:
    def __init__(self) -> None:
        self.picks: list[int] = []

    def pick_added(self, pick: PickTime) -> None:
        self.picks.append(pick.id)


async def test_voice_reply_uses_picks_page_and_voice_note(session: RepoSession) -> None:
    session.add_pick(make_pick(1))
    text = await collect(session.voice_reply("make [1] red", [1], continuation=False))
    assert text == "On it. Queued."
    talker = FakeTalker.instances[-1]
    prompt = talker.prompts[-1]
    assert talker.spoken == [True]
    assert prompt.startswith("[Worker] no tasks yet.")
    assert "[Voice] The user paused mid-sentence" not in prompt
    assert 'Page: "Cards" — http://localhost:5173/cards' in prompt
    assert "A screenshot" not in prompt
    assert "User: make [1] red\nPicks:\n[1] div.card-header" in prompt
    task = session.queue.all_tasks()[-1]
    assert task.pick_ids == [1]
    assert task.page is not None and task.page.route == "/cards"


async def test_continuation_note_and_last_typed_page(session: RepoSession) -> None:
    page = PageContext(
        url="http://localhost:5173/x",
        route="/x",
        title="X",
        viewport=Viewport(w=1, h=1, dpr=1),
        screenshot="ab" * 16,
    )
    await session.submit_user_text("chat", [], page)
    await collect(session.voice_reply("chat", [], continuation=True))
    prompt = FakeTalker.instances[-1].prompts[-1]
    assert "[Voice] The user paused mid-sentence and carried on" in prompt
    assert 'Page: "X" — http://localhost:5173/x' in prompt
    assert "A screenshot" not in prompt  # the old screenshot isn't reused


async def test_closing_the_stream_interrupts_the_talker(session: RepoSession) -> None:
    stream = session.voice_reply("hang", [], continuation=False)
    first = await anext(stream)
    assert first == "Thinking"
    assert session.talker_busy
    await stream.aclose()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not session.talker_busy:
            break
    talker = FakeTalker.instances[-1]
    assert talker.interrupts == 1
    assert not session.talker_busy
    # The next turn hears how much of the reply the user got.
    session.voice_interrupted("Thinking")
    await collect(session.voice_reply("chat", [], continuation=False))
    assert '[System] The user interrupted you; they only heard: "Thinking"' in talker.prompts[-1]


async def test_voice_and_typed_turns_are_serialised(session: RepoSession) -> None:
    stream = session.voice_reply("hang", [], continuation=False)
    assert await anext(stream) == "Thinking"
    typed = session.submit_user_text("chat")  # interrupts the voice reply first (SPEC §9.2)
    rest = await collect(stream)
    await typed
    call_talker, typed_talker = FakeTalker.instances[-2:]
    assert rest == ""
    assert call_talker.interrupts == 1
    assert [p.splitlines()[-1] for p in call_talker.prompts] == ["User: hang"]
    assert [p.splitlines()[-1] for p in typed_talker.prompts] == ["User: chat"]


async def test_task_events_feed_the_announcer_and_notify(session: RepoSession) -> None:
    out = capture(session)
    session._enqueue(make_brief("Red heading"), None)  # pyright: ignore[reportPrivateUsage]
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not session.queue.active:
            break
    notes = [m for m in out if m.type == "notify"]
    assert [n.payload.action for n in notes] == ["call"]  # no call: click starts one
    assert session.has_announcements()
    stream = session.voice_announcement(opening=True)
    assert stream is not None
    assert await collect(stream) == "Update."
    prompt = FakeTalker.instances[-1].prompts[-1]
    assert "[System] Task 'Red heading' completed" in prompt
    assert "just started a voice call" in prompt
    assert not session.has_announcements()
    assert session.voice_announcement(opening=False) is None


async def test_typed_turn_marks_announcements_seen(session: RepoSession) -> None:
    session._enqueue(make_brief("Red heading"), None)  # pyright: ignore[reportPrivateUsage]
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not session.queue.active:
            break
    assert session.has_announcements()
    await session.submit_user_text("chat")
    assert not session.has_announcements()


async def test_during_a_call_results_are_announced_not_notified(session: RepoSession) -> None:
    out = capture(session)
    call = StubCall()
    session.attach_call(call)  # pyright: ignore[reportArgumentType]
    session._enqueue(make_brief("approve"), None)  # pyright: ignore[reportPrivateUsage]
    for _ in range(100):
        await asyncio.sleep(0.01)
        if session.approvals.pending():
            break
    blocked = session.announcer.pending()
    assert [(e.kind, e.title) for e in blocked] == [("blocked", "approve")]
    assert "pnpm add zod" in blocked[0].detail
    assert [m.payload.action for m in out if m.type == "notify"] == ["open"]
    session.respond_approval(session.approvals.pending()[0].id, True)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not session.queue.active:
            break
    # The approval was answered, then the task completed: only "completed" remains.
    assert [e.kind for e in session.announcer.pending()] == ["completed"]
    assert [m.payload.action for m in out if m.type == "notify"] == ["open"]
    session.add_pick(make_pick(5))
    assert call.picks == [5]
    session.detach_call(call)  # pyright: ignore[reportArgumentType]
    assert not session.call_active


async def test_warm_talker_connects_once(session: RepoSession) -> None:
    session.warm_talker()
    await asyncio.sleep(0.01)
    assert FakeTalker.instances[-1].warmed == 1
    await collect(session.voice_reply("chat", [], continuation=False))
    assert len(FakeTalker.instances) == 1


async def test_call_talker_is_seeded_once_then_rotated(
    session: RepoSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sidecall.session.MAX_CALL_TALKER_TURNS", 2)
    session._add_transcript("user", "make it red")  # pyright: ignore[reportPrivateUsage]
    session._add_transcript("agent", "On it.")  # pyright: ignore[reportPrivateUsage]
    await collect(session.voice_reply("chat", [], continuation=False))
    first = FakeTalker.instances[-1]
    assert first.prompts[-1].startswith(
        "[Earlier in this call]\nThey said: make it red\nYou said: On it.\n[Worker]"
    )
    await collect(session.voice_reply("chat", [], continuation=False))
    assert first.prompts[-1].startswith("[Worker]")
    # the third turn gets a fresh talker, seeded again
    await collect(session.voice_reply("chat", [], continuation=False))
    assert FakeTalker.instances[-1] is not first
    assert FakeTalker.instances[-1].prompts[-1].startswith("[Earlier in this call]")


async def test_finished_tasks_go_in_the_task_log(session: RepoSession) -> None:
    task = session._enqueue(make_brief("Red heading"), None, request="make it red")  # pyright: ignore[reportPrivateUsage]
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not session.queue.active:
            break
    [entry] = session.task_log.recent()
    assert entry.title == task.title and entry.status == "completed"
    assert entry.request.endswith("(user said: make it red)")
    assert "Earlier tasks in this repo" in session.worker_seed()


async def test_hang_up_after_on_it_still_dispatches(session: RepoSession) -> None:
    """The 13:59:28 bug: "On it" is spoken, the user hangs up (the pipeline closes the
    reply stream) before the talker has emitted its tool call. The task must still run."""
    stream = session.voice_reply("pause: add a dark mode toggle", [], continuation=False)
    assert await anext(stream) == "On it. "
    await stream.aclose()  # what pipeline cancellation does to the reply
    session.detach_call(session._call)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    for _ in range(100):
        await asyncio.sleep(0.01)
        done = [t for t in session.queue.all_tasks() if t.status == "completed"]
        if done:
            break
    [task] = session.queue.all_tasks()
    assert task.title == "pause: add a dark mode toggle"
    assert task.status == "completed"
    assert FakeTalker.instances[-1].wound_down == 1
    assert FakeTalker.instances[-1].interrupts == 0


async def test_costs_add_up_and_reach_the_extension(session: RepoSession) -> None:
    out = capture(session)
    session.add_cost("call", 0.004)
    session.add_cost("worker", 0.05)
    session.add_cost("call", 0.002)
    session.add_cost("talker", None)  # unknown: not counted, nothing sent
    costs = session.state().costs
    assert (round(costs.call, 4), costs.worker, costs.talker) == (0.006, 0.05, 0.0)
    sent = [m for m in out if m.type == "costs.state"]
    assert len(sent) == 3 and sent[-1].payload == costs


async def test_clear_chat_empties_it_and_starts_a_fresh_typed_talker(
    session: RepoSession, tmp_path: Path
) -> None:
    out = capture(session)
    await session.submit_user_text("chat")
    first = FakeTalker.instances[-1]
    session.sessions.set_talker_session("old-session")
    session.clear_chat()
    assert session.transcript.recent() == []
    assert session.sessions.data.talker_session_id is None
    assert any(m.type == "chat.cleared" for m in out)
    await session.submit_user_text("chat")
    assert FakeTalker.instances[-1] is not first  # a new talker, so a new session
    # a restart doesn't bring the cleared entries back (the files still have them)
    again = RepoSession(
        session.repo_path, tmp_path / "home", lambda _m: None, FakeTalker, FakeWorker
    )
    assert [e.text for e in again.transcript.recent()] == ["chat", "Hello there."]


async def test_clear_chat_daily(session: RepoSession) -> None:
    from sidecall.session import is_new_day

    day = 24 * 3600 * 1000
    noon = 1_790_000_000_000.0
    assert is_new_day(noon, noon + day) and not is_new_day(noon, noon + 60_000)
    session._add_transcript("user", "yesterday")  # pyright: ignore[reportPrivateUsage]
    last = session.transcript.recent()[-1].ts
    assert not session.clear_chat_if_new_day(last + 60_000)
    session.set_clear_chat_daily(False)
    assert not session.clear_chat_if_new_day(last + day)
    session.set_clear_chat_daily(True)
    assert session.clear_chat_if_new_day(last + day)
    assert session.transcript.recent() == []
