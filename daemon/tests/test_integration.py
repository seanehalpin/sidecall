"""Integration tests against the real Claude Code (haiku). Run with `pytest --integration`.

Each uses a tiny scratch git repo in tmp_path, never a tracked fixture.
"""

from __future__ import annotations

import asyncio
import shutil
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
from sidecall.protocol.models import Brief, Pick, Preset, Target, Task
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
    def __init__(
        self,
        tmp_path: Path,
        talker: bool,
        worker: WorkerFactory = ClaudeWorker,
        repo: Path | None = None,
        real_worker: bool = False,
    ) -> None:
        self.repo = repo or scratch_repo(tmp_path)
        self.messages: list[ServerMessage] = []
        self.finished = asyncio.Event()
        self.auto_approve: bool | None = None
        self.session = RepoSession(
            self.repo,
            tmp_path / "home",
            emit=self.emit,
            talker_factory=ClaudeTalker,
            worker_factory=IdleWorker if talker and not real_worker else worker,
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


# --- picks (Milestone 2) -----------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def fixture_copy(
    name: str, tmp_path: Path, edits: dict[str, tuple[str, str]] | None = None
) -> Path:
    """A tiny git-tracked temp copy of a fixture app (never the real fixtures/ dir)."""
    repo = tmp_path / name
    shutil.copytree(
        FIXTURES / name, repo, ignore=shutil.ignore_patterns("node_modules", ".svelte-kit")
    )
    for rel, (old, new) in (edits or {}).items():
        path = repo / rel
        text = path.read_text()
        assert old in text, (rel, old)
        path.write_text(text.replace(old, new))
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    return repo


def git_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.split()


def git_diff(repo: Path) -> str:
    out = subprocess.run(["git", "diff"], cwd=repo, check=True, capture_output=True, text=True)
    return out.stdout


def line_of(path: Path, needle: str) -> int:
    for i, text in enumerate(path.read_text().splitlines(), 1):
        if needle in text:
            return i
    raise AssertionError(f"{needle!r} not in {path}")


def raw_pick(
    pick_id: int,
    *,
    framework: str,
    version: str,
    file: str,
    line: int,
    column: int,
    tag: str,
    classes: str,
    text: str,
    styles: dict[str, str],
    chain: list[dict[str, Any]],
    repeat: tuple[int, int] | None = None,
) -> Pick:
    return Pick.model_validate(
        {
            "id": pick_id,
            "createdAt": 1000.0 + pick_id,
            "kind": "element",
            "page": {
                "url": "http://localhost:5173/",
                "route": "/",
                "title": "Projects",
                "viewport": {"w": 1280, "h": 800, "dpr": 2},
                "scroll": {"x": 0, "y": 0},
                "colorScheme": "light",
                "consoleErrors": [],
            },
            "element": {
                "selector": f"{tag}.{classes}",
                "altSelectors": [f'[data-testid="{classes}"]'],
                "tag": tag,
                "attributes": {"class": classes},
                "text": text,
                "outerHTML": f'<{tag} class="{classes}">{text}</{tag}>',
                "rect": {"x": 300, "y": 120, "w": 220, "h": 24},
                "computedStyles": styles,
            },
            "framework": {
                "name": framework,
                "version": version,
                "source": {"file": file, "line": line, "column": column, "precision": "element"},
                "componentChain": chain,
            },
            "repeat": {"count": repeat[0], "index": repeat[1]} if repeat else None,
        }
    )


async def talk_until_dispatch(h: Harness, text: str, pick_ids: list[int]) -> list[str]:
    """Send `text`; if the talker asks a question instead of dispatching, say go ahead
    (at most twice). Returns the agent replies."""
    replies: list[str] = []
    for attempt in range(3):
        before = len(h.of(TaskCreated))
        await h.session.submit_user_text(
            text if attempt == 0 else "Yes, go ahead and do it, no need to ask again.",
            pick_ids if attempt == 0 else [],
        )
        entries = [e for e in h.session.transcript.recent() if e.role == "agent"]
        replies.append(entries[-1].text if entries else "")
        if len(h.of(TaskCreated)) > before:
            return replies
    raise AssertionError(f"the talker never dispatched: {replies}")


def worker_texts(h: Harness) -> list[str]:
    return [e.payload.text for e in h.of(TaskEvent) if e.payload.kind == "text"]


async def test_picks_make_card_title_match_sidebar_link(tmp_path: Path) -> None:
    # A visible difference to match: distinctive colour, padding and letter-spacing on links.
    repo = fixture_copy(
        "svelte5-app",
        tmp_path,
        {
            "src/routes/components/Sidebar.svelte": (
                "a { display: block; padding: 8px; color: #333;",
                "a { display: block; padding: 14px 22px; color: #0a7d4b; letter-spacing: 0.04em;",
            )
        },
    )
    assert "#0a7d4b" in (repo / "src/routes/components/Sidebar.svelte").read_text()
    h = Harness(tmp_path, talker=True, real_worker=True, repo=repo)
    card = "src/routes/components/Card.svelte"
    side = "src/routes/components/Sidebar.svelte"
    h.session.add_pick(
        raw_pick(
            1,
            framework="svelte",
            version="5",
            file=card,
            line=line_of(repo / card, '<h2 class="card-title"'),
            column=2,
            tag="h2",
            classes="card-title",
            text="Design review",
            repeat=(12, 2),
            styles={
                "padding": "0px",
                "margin": "0px 0px 8px",
                "font-size": "16px",
                "font-weight": "700",
                "color": "rgb(0, 0, 0)",
                "display": "block",
            },
            chain=[
                {
                    "name": "Card",
                    "type": "component",
                    "file": "src/routes/components/CardList.svelte",
                    "line": 8,
                    "column": 2,
                },
                {
                    "name": "each",
                    "type": "each",
                    "file": "src/routes/components/CardList.svelte",
                    "line": 7,
                    "column": 1,
                },
                {
                    "name": "CardList",
                    "type": "component",
                    "file": "src/routes/+page.svelte",
                    "line": 12,
                    "column": 2,
                },
            ],
        )
    )
    h.session.add_pick(
        raw_pick(
            2,
            framework="svelte",
            version="5",
            file=side,
            line=line_of(repo / side, "<a"),
            column=4,
            tag="a",
            classes="nav-link",
            text="Inbox",
            repeat=(5, 0),
            styles={
                "padding": "14px 22px",
                "margin": "0px",
                "font-size": "16px",
                "font-weight": "400",
                "color": "rgb(10, 125, 75)",
                "display": "block",
                "letter-spacing": "0.64px",
            },
            chain=[
                {
                    "name": "Sidebar",
                    "type": "component",
                    "file": "src/routes/+page.svelte",
                    "line": 9,
                    "column": 1,
                },
            ],
        )
    )
    h.session.start()
    try:
        replies = await talk_until_dispatch(h, "make [1] match [2]", [1, 2])
        task: Task = h.of(TaskCreated)[-1].payload
        await asyncio.wait_for(h.finished.wait(), 300)
        files = git_files(repo)
        diff = git_diff(repo)
        print("\nTALKER:", replies)
        print("BRIEF:", task.brief.model_dump_json(indent=1))
        print("WORKER:", worker_texts(h)[-3:], [e.payload.summary for e in h.of(TaskCompleted)])
        print("FILES:", files)
        print("DIFF:\n" + diff)
        assert h.of(TaskCompleted), [m.type for m in h.messages]
        assert task.pick_ids == [1, 2]
        assert "src/routes/components/Card.svelte" in files
        assert "src/routes/components/CardList.svelte" not in files
        assert "src/routes/+page.svelte" not in files
        added = "\n".join(ln for ln in diff.splitlines() if ln.startswith("+"))
        assert any(v in added for v in ("#0a7d4b", "10, 125, 75", "14px 22px", "0.04em")), added
    finally:
        await h.session.close()


async def test_picks_repeated_card_title_edits_the_shared_component(tmp_path: Path) -> None:
    repo = fixture_copy("react19-app", tmp_path)
    h = Harness(tmp_path, talker=True, real_worker=True, repo=repo)
    card = "src/components/Card.tsx"
    h.session.add_pick(
        raw_pick(
            1,
            framework="react",
            version="19.3.0",
            file=f"http://localhost:5275/{card}?t=1760000000000",
            line=line_of(repo / card, '<h2 className="card-title"'),
            column=8,
            tag="h2",
            classes="card-title",
            text="Design review",
            repeat=(12, 2),
            styles={
                "padding": "0px",
                "margin": "0px 0px 8px",
                "font-size": "16px",
                "font-weight": "400",
                "color": "rgb(0, 0, 0)",
                "display": "block",
            },
            chain=[
                {
                    "name": "Card",
                    "type": "function",
                    "file": "http://localhost:5275/src/components/CardList.tsx?t=1760000000000",
                    "line": 8,
                    "column": 8,
                },
                {
                    "name": "CardList",
                    "type": "function",
                    "file": "http://localhost:5275/src/App.tsx?t=1760000000000",
                    "line": 12,
                    "column": 6,
                },
            ],
        )
    )
    h.session.start()
    try:
        replies = await talk_until_dispatch(h, "make [1] bold", [1])
        task: Task = h.of(TaskCreated)[-1].payload
        await asyncio.wait_for(h.finished.wait(), 300)
        files = git_files(repo)
        diff = git_diff(repo)
        print("\nTALKER:", replies)
        print("BRIEF:", task.brief.model_dump_json(indent=1))
        print("WORKER:", worker_texts(h)[-3:], [e.payload.summary for e in h.of(TaskCompleted)])
        print("FILES:", files)
        print("DIFF:\n" + diff)
        assert h.of(TaskCompleted), [m.type for m in h.messages]
        assert task.pick_ids == [1]
        assert files and set(files) <= {"src/components/Card.tsx", "src/styles.css"}, files
        assert "src/data.ts" not in files
        added = "\n".join(ln for ln in diff.splitlines() if ln.startswith("+")).lower()
        assert any(w in added for w in ("bold", "700", "600", "<strong")), added
    finally:
        await h.session.close()
