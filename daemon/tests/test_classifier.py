from __future__ import annotations

import os
from pathlib import Path

import pytest

from sidecall.classifier import (
    GIT_PUSH_REASON,
    Decision,
    classify,
    classify_command,
    combine,
    split_command,
    strip_wrappers,
)
from sidecall.protocol.models import Preset

ALL_PRESETS: list[Preset] = ["cautious", "default", "permissive"]

# (command, cautious, default, permissive)
BASH_CASES: list[tuple[str, str, str, str]] = [
    # plain commands
    ("ls -la", "ask", "allow", "allow"),
    ("git status", "ask", "allow", "allow"),
    ("pnpm test", "ask", "allow", "allow"),
    ("npm run build", "ask", "allow", "allow"),
    ("npm run install", "ask", "allow", "allow"),
    ("cat src/app.css | grep color", "ask", "allow", "allow"),
    ("", "allow", "allow", "allow"),
    ("   ", "allow", "allow", "allow"),
    # git push, every form -> deny everywhere
    ("git push", "deny", "deny", "deny"),
    ("git push origin main", "deny", "deny", "deny"),
    ("git push -f", "deny", "deny", "deny"),
    ("git push --force-with-lease origin HEAD", "deny", "deny", "deny"),
    ("git -C ../other push", "deny", "deny", "deny"),
    ("git -c user.name=x push", "deny", "deny", "deny"),
    ("git -c push.default=current push", "deny", "deny", "deny"),
    ("git --no-pager push", "deny", "deny", "deny"),
    ("git --git-dir=.git push", "deny", "deny", "deny"),
    ("git --work-tree . push", "deny", "deny", "deny"),
    ("/usr/bin/git push", "deny", "deny", "deny"),
    ("git -c alias.p=push p", "deny", "deny", "deny"),
    ("git -c 'alias.sync=!git fetch && git push' sync", "deny", "deny", "deny"),
    ("git subtree push --prefix dist origin gh-pages", "deny", "deny", "deny"),
    ("git add . && git commit -m 'x' && git push", "deny", "deny", "deny"),
    ("git commit -m 'push it' ", "ask", "allow", "allow"),
    ('git commit -m "fix; git push"', "ask", "allow", "allow"),
    ("echo $(git push)", "deny", "deny", "deny"),
    ("echo `git push`", "deny", "deny", "deny"),
    ('echo "result: $(git push origin)"', "deny", "deny", "deny"),
    ("cd ../repo && git push", "deny", "deny", "deny"),
    ("cd ../repo; git push", "deny", "deny", "deny"),
    ("true || git push", "deny", "deny", "deny"),
    ("git fetch & git push", "deny", "deny", "deny"),
    ("git status\ngit push", "deny", "deny", "deny"),
    ("(cd x && git push)", "deny", "deny", "deny"),
    ("{ git push; }", "deny", "deny", "deny"),
    ("sudo git push", "deny", "deny", "deny"),
    ("sudo -u bob git push", "deny", "deny", "deny"),
    ("env GIT_SSH=x git push", "deny", "deny", "deny"),
    ("GIT_TRACE=1 git push", "deny", "deny", "deny"),
    ("command git push", "deny", "deny", "deny"),
    ("time git push", "deny", "deny", "deny"),
    ("nice -n 10 git push", "deny", "deny", "deny"),
    ("timeout 30 git push", "deny", "deny", "deny"),
    ("bash -c 'git push'", "deny", "deny", "deny"),
    ('sh -c "cd x && git push"', "deny", "deny", "deny"),
    ("eval git push", "deny", "deny", "deny"),
    ("echo main | xargs git push origin", "deny", "deny", "deny"),
    ("git log 2>&1 | head; git push", "deny", "deny", "deny"),
    ("echo 'git push'", "ask", "allow", "allow"),
    ("grep -r 'git push' docs", "ask", "allow", "allow"),
    ("# git push\nls", "ask", "allow", "allow"),
    # package installs
    ("pnpm add zod", "ask", "ask", "allow"),
    ("pnpm install", "ask", "ask", "allow"),
    ("pnpm i", "ask", "ask", "allow"),
    ("pnpm -C web add react", "ask", "ask", "allow"),
    ("pnpm --filter web add react", "ask", "ask", "allow"),
    ("npm install", "ask", "ask", "allow"),
    ("npm i -D vitest", "ask", "ask", "allow"),
    ("npm install --save-dev @types/node", "ask", "ask", "allow"),
    ("npm ci", "ask", "ask", "allow"),
    ("yarn", "ask", "ask", "allow"),
    ("yarn add lodash", "ask", "ask", "allow"),
    ("yarn global add serve", "ask", "ask", "allow"),
    ("yarn workspace web add zod", "ask", "ask", "allow"),
    ("yarn test", "ask", "allow", "allow"),
    ("bun add hono", "ask", "ask", "allow"),
    ("bun install", "ask", "ask", "allow"),
    ("bun run dev", "ask", "allow", "allow"),
    ("pip install requests", "ask", "ask", "allow"),
    ("pip3 install -r requirements.txt", "ask", "ask", "allow"),
    ("python -m pip install httpx", "ask", "ask", "allow"),
    ("python3 -m pip install --upgrade pip", "ask", "ask", "allow"),
    ("python3 -m pytest", "ask", "allow", "allow"),
    ("uv add pydantic", "ask", "ask", "allow"),
    ("uv pip install rich", "ask", "ask", "allow"),
    ("uv run pytest", "ask", "allow", "allow"),
    ("brew install jq", "ask", "ask", "allow"),
    ("brew list", "ask", "allow", "allow"),
    ("cd web && pnpm add zod", "ask", "ask", "allow"),
    ("CI=1 npm install", "ask", "ask", "allow"),
    ("npx prettier --write .", "ask", "allow", "allow"),
    ("pnpm add zod && git push", "deny", "deny", "deny"),
    # destructive git -> ask in all presets
    ("git reset --hard", "ask", "ask", "ask"),
    ("git reset --hard HEAD~1", "ask", "ask", "ask"),
    ("git reset --soft HEAD~1", "ask", "allow", "allow"),
    ("git clean -f", "ask", "ask", "ask"),
    ("git clean -fd", "ask", "ask", "ask"),
    ("git clean -fdx", "ask", "ask", "ask"),
    ("git clean -d -f", "ask", "ask", "ask"),
    ("git clean -xdf", "ask", "ask", "ask"),
    ("git clean --force", "ask", "ask", "ask"),
    ("git clean -n", "ask", "allow", "allow"),
    ("git checkout -- .", "ask", "ask", "ask"),
    ("git checkout .", "ask", "ask", "ask"),
    ("git checkout HEAD -- src/app.css", "ask", "ask", "ask"),
    ("git checkout -b feature", "ask", "allow", "allow"),
    ("git checkout main", "ask", "allow", "allow"),
    ("git restore .", "ask", "ask", "ask"),
    ("git restore --staged .", "ask", "ask", "ask"),
    ("git restore --worktree --staged .", "ask", "ask", "ask"),
    ("git restore src/app.css", "ask", "allow", "allow"),
    ("git -C sub reset --hard", "ask", "ask", "ask"),
]


@pytest.mark.parametrize(("command", "cautious", "default", "permissive"), BASH_CASES)
def test_bash_commands(command: str, cautious: str, default: str, permissive: str) -> None:
    expected = {"cautious": cautious, "default": default, "permissive": permissive}
    for preset in ALL_PRESETS:
        got = classify("Bash", {"command": command}, Path("/repo"), preset)
        assert got.action == expected[preset], f"{command!r} [{preset}] -> {got}"


def test_git_push_reason() -> None:
    assert classify_command("git push", "permissive").reason == GIT_PUSH_REASON


def test_install_reason_names_the_command() -> None:
    d = classify_command("pnpm add zod", "default")
    assert d == Decision("ask", "package install: pnpm add zod")


def test_destructive_reason() -> None:
    assert classify_command("git reset --hard", "default").reason.startswith("destructive git:")


def test_split_command_extracts_substitutions_and_respects_quotes() -> None:
    assert split_command("a && b || c; d | e & f") == ["a", "b", "c", "d", "e", "f"]
    assert split_command("echo 'a && b'") == ["echo 'a && b'"]
    assert split_command('echo "x $(git push) y"') == ["git push", 'echo "x __subst__ y"']
    assert split_command("ls 2>&1 >/dev/null") == ["ls 2>&1 >/dev/null"]
    assert split_command("echo $((1 + 2))") == ["echo 0"]


def test_strip_wrappers() -> None:
    assert strip_wrappers(["sudo", "-E", "FOO=1", "env", "-i", "A=b", "git", "push"]) == [
        "git",
        "push",
    ]
    assert strip_wrappers(["nice", "-n", "5", "time", "-p", "ls"]) == ["ls"]


def test_unbalanced_quotes_do_not_raise() -> None:
    assert classify_command("echo 'oops && git push", "default").action in ("allow", "deny")
    assert classify_command('git push "unterminated', "default").action == "deny"


def test_combine_prefers_deny_then_ask() -> None:
    a, b, c = Decision("allow", "x"), Decision("ask", "y"), Decision("deny", "z")
    assert combine([a, b, c]).action == "deny"
    assert combine([a, b]).action == "ask"
    assert combine([]).action == "allow"
    assert combine([Decision("ask", "p"), Decision("ask", "q")]).reason == "p; q"


# --- edits ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.css").write_text("")
    return root


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_edits_inside_repo_are_allowed(repo: Path, tool: str) -> None:
    for path in ["src/app.css", str(repo / "src" / "app.css"), "src/new/file.ts", "./x.txt"]:
        assert classify(tool, {"file_path": path}, repo, "cautious").action == "allow", path


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_edits_outside_repo_are_denied(repo: Path, tool: str) -> None:
    for path in ["/tmp/x.txt", "../outside.txt", "src/../../outside.txt", "~/.zshrc"]:
        d = classify(tool, {"file_path": path}, repo, "permissive")
        assert d.action == "deny", path
        assert "outside the repo" in d.reason


def test_symlink_escaping_repo_is_denied(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo / "link")
    assert classify("Write", {"file_path": "link/evil.txt"}, repo, "default").action == "deny"


def test_repo_given_via_symlink_still_matches(repo: Path, tmp_path: Path) -> None:
    alias = tmp_path / "alias"
    os.symlink(repo, alias)
    d = classify("Edit", {"file_path": str(repo / "src/app.css")}, alias, "default")
    assert d.action == "allow"


def test_notebook_edit_uses_notebook_path(repo: Path) -> None:
    assert classify("NotebookEdit", {"notebook_path": "a.ipynb"}, repo, "default").action == "allow"
    assert classify("NotebookEdit", {"notebook_path": "/tmp/a.ipynb"}, repo, "default").action == (
        "deny"
    )


def test_edit_without_path_is_denied(repo: Path) -> None:
    assert classify("Edit", {}, repo, "default").action == "deny"


def test_other_tools_are_allowed(repo: Path) -> None:
    for tool in ["Read", "Grep", "Glob", "WebFetch", "TodoWrite"]:
        assert classify(tool, {"file_path": "/etc/hosts"}, repo, "cautious").action == "allow"
