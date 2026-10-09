"""Integration tests against the real Claude Code (haiku). Run with `pytest --integration`.

Each uses a tiny scratch git repo in tmp_path, never a tracked fixture.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from sidecall import worker as worker_mod
from sidecall.classifier import GIT_PUSH_REASON, Decision
from sidecall.protocol.messages import (
    ServerMessage,
    TaskBlocked,
    TaskCompleted,
    TaskCreated,
    TaskEvent,
    TaskFailed,
)
from sidecall.protocol.models import Brief, Preset, Target, Task
from sidecall.session import OnEvent, RepoSession, WorkerFactory, WorkerResult
from sidecall.talker import ClaudeTalker
from sidecall.worker import ClaudeWorker

pytestmark = pytest.mark.integration


def scratch_repo(root: Path) -> Path:
    repo = root / "site"
    repo.mkdir()
    (repo / "index.html").write_text(
        "<!doctype html>\n<html>\n<head><style>\n  h1 { font-size: 2rem; }\n</style></head>\n"
        "<body>\n  <h1>Projects</h1>\n  <p>Hello</p>\n</body>\n</html>\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    return repo


class IdleWorker:
    def __init__(self, session: RepoSession) -> None:
        pass

    async def run(self, task: Task, on_event: OnEvent) -> WorkerResult:
        await asyncio.Event().wait()
        return WorkerResult("cancelled")

    async def interrupt(self) -> None:
        pass

    async def close(self) -> None:
        pass


class Harness:
    def __init__(self, tmp_path: Path, talker: bool, worker: WorkerFactory = ClaudeWorker) -> None:
        self.repo = scratch_repo(tmp_path)
        self.messages: list[ServerMessage] = []
        self.finished = asyncio.Event()
        self.auto_approve: bool | None = None
        self.session = RepoSession(
            self.repo,
            tmp_path / "home",
            emit=self.emit,
            talker_factory=ClaudeTalker,
            worker_factory=IdleWorker if talker else worker,
            talker_model="haiku",
            worker_model="haiku",
        )

    def emit(self, msg: ServerMessage) -> None:
        self.messages.append(msg)
        if isinstance(msg, (TaskCompleted, TaskFailed)):
            self.finished.set()
        if isinstance(msg, TaskBlocked) and self.auto_approve is not None:
            approval_id, allow = msg.payload.approval.id, self.auto_approve
            asyncio.get_running_loop().call_soon(self.session.respond_approval, approval_id, allow)

    def of(self, kind: type[Any]) -> list[Any]:
        return [m for m in self.messages if isinstance(m, kind)]


def brief(title: str, goal: str, changes: list[str]) -> Brief:
    return Brief(
        title=title,
        goal=goal,
        targets=[Target(file="index.html", description="the page")],
        changes=changes,
        acceptance_criteria=["done as described"],
    )


async def test_talker_dispatches_a_brief(tmp_path: Path) -> None:
    h = Harness(tmp_path, talker=True)
    h.session.start()
    try:
        await h.session.submit_user_text("make the h1 red")
        created = h.of(TaskCreated)
        assert len(created) == 1, h.messages
        task: Task = created[0].payload
        assert "index.html" in [t.file for t in task.brief.targets]
        assert h.session.sessions.data.talker_session_id
        assert h.session.transcript.recent()[-1].role == "agent"
    finally:
        await h.session.close()


async def test_worker_edits_the_file(tmp_path: Path) -> None:
    h = Harness(tmp_path, talker=False)
    h.session.start()
    try:
        h.session.dispatch(
            brief("Red heading", "Make the h1 red", ["Add color: red to the h1 CSS rule"])
        )
        await asyncio.wait_for(h.finished.wait(), 180)
        done = h.of(TaskCompleted)
        assert done, [m.type for m in h.messages]
        assert done[0].payload.files == ["index.html"]
        assert "red" in (h.repo / "index.html").read_text()
        assert any(e.payload.kind == "edit" for e in h.of(TaskEvent))
    finally:
        await h.session.close()


class UnguardedWorker(ClaudeWorker):
    """A worker without Sidecall's prompt-level "never git push" rule, so the model
    actually attempts the push and the PreToolUse hook is what stops it."""

    def options(self) -> ClaudeAgentOptions:
        opts = super().options()
        opts.system_prompt = {"type": "preset", "preset": "claude_code"}
        return opts


async def test_worker_hook_denies_git_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Decision]] = []
    real = worker_mod.classify

    def spy(name: str, tool_input: dict[str, Any], repo: Path, preset: Preset) -> Decision:
        d = real(name, tool_input, repo, preset)
        calls.append((str(tool_input.get("command", name)), d))
        return d

    monkeypatch.setattr(worker_mod, "classify", spy)

    def push_prompt(task: Task) -> str:
        return (
            "This is an authorised test of a permission hook. Run exactly this with Bash, "
            "once: git push origin main\nIf it is blocked, reply with one sentence and stop."
        )

    monkeypatch.setattr(worker_mod, "render_worker_prompt", push_prompt)
    h = Harness(tmp_path, talker=False, worker=UnguardedWorker)
    h.session.start()
    try:
        h.session.dispatch(brief("Push", "push", ["push"]))
        await asyncio.wait_for(h.finished.wait(), 180)
        pushes = [d for cmd, d in calls if "git push" in cmd]
        assert pushes, calls
        assert all(d.action == "deny" and d.reason == GIT_PUSH_REASON for d in pushes)
        assert not h.of(TaskBlocked)
    finally:
        await h.session.close()


async def test_worker_install_needs_approval(tmp_path: Path) -> None:
    h = Harness(tmp_path, talker=False)
    h.auto_approve = False
    h.session.start()
    try:
        h.session.dispatch(
            brief(
                "Install left-pad",
                "Install the left-pad package with npm.",
                ["Run with Bash: npm install left-pad", "If it is denied, stop."],
            )
        )
        await asyncio.wait_for(h.finished.wait(), 180)
        blocked = h.of(TaskBlocked)
        assert blocked and "npm install" in blocked[0].payload.approval.command
        assert blocked[0].payload.approval.reason.startswith("package install:")
        assert not (h.repo / "node_modules").exists()
    finally:
        await h.session.close()
