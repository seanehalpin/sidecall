"""Permission classifier for worker tool calls (SPEC §7). Pure: no I/O beyond `realpath`.

`classify(tool_name, tool_input, repo_root, preset)` returns a `Decision`:

- Edit/Write/MultiEdit/NotebookEdit: inside the repo -> allow, outside -> deny.
- Bash: the command is split into simple commands (on `&& || ; | &`, newlines,
  subshell parens, and the contents of `$( )`, backticks and `sh -c` strings).
  Each segment is classified and the strictest result wins (deny > ask > allow):
  - any form of `git push` -> deny in every preset;
  - package installs -> ask (cautious, default) or allow (permissive);
  - destructive git (`reset --hard`, `clean -f…`, `checkout .`, `restore .`) -> ask;
  - anything else -> ask in cautious, allow otherwise.
- Every other tool -> allow.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .protocol.models import Preset

Action = Literal["allow", "deny", "ask"]

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
GIT_PUSH_REASON = "git push is never allowed"

_RANK: dict[Action, int] = {"allow": 0, "ask": 1, "deny": 2}


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str


ALLOW = Decision("allow", "allowed")


def classify(
    tool_name: str, tool_input: dict[str, Any], repo_root: Path, preset: Preset
) -> Decision:
    if tool_name in EDIT_TOOLS:
        return _classify_edit(tool_input, repo_root)
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return Decision("deny", "missing shell command")
        return classify_command(command, preset)
    return ALLOW


# --- Edits -----------------------------------------------------------------------


def _classify_edit(tool_input: dict[str, Any], repo_root: Path) -> Decision:
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return Decision("deny", "edit without a file path")
    if is_inside(raw, repo_root):
        return Decision("allow", "edit inside the repo")
    return Decision("deny", f"write outside the repo: {raw}")


def resolve_path(path: str, repo_root: Path) -> Path:
    """Resolve `path` (absolute, or relative to the repo) to a normalised real path."""
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = repo_root / p
    return Path(os.path.realpath(p))


def is_inside(path: str, repo_root: Path) -> bool:
    root = Path(os.path.realpath(repo_root))
    return resolve_path(path, repo_root).is_relative_to(root)


# --- Bash: splitting -------------------------------------------------------------


def split_command(command: str) -> list[str]:
    """Split a shell command into simple-command segments, including the contents of
    command substitutions (recursively). Quote-aware; never raises."""
    segments: list[str] = []
    _split_into(command, segments, depth=0)
    return [s for s in (seg.strip() for seg in segments) if s]


def _split_into(command: str, out: list[str], depth: int) -> None:
    if depth > 8:
        out.append(command)
        return
    current: list[str] = []
    i, n = 0, len(command)

    def flush() -> None:
        out.append("".join(current))
        current.clear()

    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n:
            current.append(command[i : i + 2])
            i += 2
        elif c == "'":
            end = command.find("'", i + 1)
            end = n - 1 if end == -1 else end
            current.append(command[i : end + 1])
            i = end + 1
        elif c == '"':
            i = _scan_double_quoted(command, i, current, out, depth)
        elif c == "`":
            end = _find_backtick(command, i + 1)
            _split_into(command[i + 1 : end], out, depth + 1)
            current.append(" __subst__ ")
            i = end + 1
        elif c == "$" and command.startswith("$((", i):
            end = _find_close_paren(command, i + 3)
            end = _find_close_paren(command, end + 1) if end + 1 < n else end
            current.append("0")  # arithmetic expansion
            i = end + 1
        elif c in "$<>" and command.startswith("(", i + 1):
            end = _find_close_paren(command, i + 2)
            _split_into(command[i + 2 : end], out, depth + 1)
            current.append(" __subst__ ")
            i = end + 1
        elif c == "&" and _is_redirect_amp(command, i):
            current.append(c)
            i += 1
        elif command.startswith(("&&", "||"), i):
            flush()
            i += 2
        elif c in ";|&\n()":
            flush()
            i += 1
        elif c == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            end = command.find("\n", i)
            i = n if end == -1 else end
        else:
            current.append(c)
            i += 1
    flush()


def _scan_double_quoted(
    command: str, i: int, current: list[str], out: list[str], depth: int
) -> int:
    """Copy a double-quoted string starting at `i`, extracting substitutions inside it."""
    n = len(command)
    current.append('"')
    i += 1
    while i < n:
        c = command[i]
        if c == "\\" and i + 1 < n:
            current.append(command[i : i + 2])
            i += 2
        elif c == '"':
            current.append('"')
            return i + 1
        elif c == "`":
            end = _find_backtick(command, i + 1)
            _split_into(command[i + 1 : end], out, depth + 1)
            current.append("__subst__")
            i = end + 1
        elif c == "$" and command.startswith("(", i + 1) and not command.startswith("((", i + 1):
            end = _find_close_paren(command, i + 2)
            _split_into(command[i + 2 : end], out, depth + 1)
            current.append("__subst__")
            i = end + 1
        else:
            current.append(c)
            i += 1
    current.append('"')
    return n


def _find_backtick(command: str, i: int) -> int:
    n = len(command)
    while i < n:
        if command[i] == "\\":
            i += 2
            continue
        if command[i] == "`":
            return i
        i += 1
    return n


def _find_close_paren(command: str, i: int) -> int:
    """Index of the `)` closing a group whose body starts at `i` (quote-aware)."""
    depth, n = 1, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            end = command.find("'", i + 1)
            i = n if end == -1 else end + 1
            continue
        if c == '"':
            j = i + 1
            while j < n and command[j] != '"':
                j += 2 if command[j] == "\\" else 1
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _is_redirect_amp(command: str, i: int) -> bool:
    """`&` that is part of a redirection: `2>&1`, `>&2`, `&>file`, `&>>file`."""
    prev = command[i - 1] if i > 0 else ""
    nxt = command[i + 1] if i + 1 < len(command) else ""
    return prev in "<>" or (nxt == ">" and not command.startswith("&&", i))


# --- Bash: per-segment classification -------------------------------------------

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SIMPLE_WRAPPERS = frozenset({"command", "builtin", "exec", "nohup", "time", "caffeinate"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "fish"})


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def strip_wrappers(words: list[str]) -> list[str]:
    """Drop leading env assignments and wrappers (`sudo`, `env`, `time`, `nice`, …)."""
    i = 0
    while i < len(words):
        w = words[i]
        base = os.path.basename(w)
        if w in ("{", "!") or _ASSIGNMENT.match(w):
            i += 1
        elif base in _SIMPLE_WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
        elif base == "sudo":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                takes_arg = words[i] in ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U")
                i += 2 if takes_arg else 1
        elif base == "env":
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGNMENT.match(words[i])):
                i += 2 if words[i] in ("-u", "-C", "-S") else 1
        elif base == "nice":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] == "-n" else 1
        elif base in ("timeout", "gtimeout"):
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
            i += 1  # the duration
        elif base == "xargs":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-I", "-n", "-P", "-L", "-d", "-E", "-s") else 1
        else:
            break
    rest = words[i:]
    while rest and rest[-1] == "}":
        rest = rest[:-1]
    return rest


def classify_command(command: str, preset: Preset) -> Decision:
    decisions = [_classify_segment(seg, preset) for seg in split_command(command)]
    return combine(decisions)


def combine(decisions: list[Decision]) -> Decision:
    if not decisions:
        return ALLOW
    worst = max(_RANK[d.action] for d in decisions)
    chosen = [d for d in decisions if _RANK[d.action] == worst]
    if chosen[0].action == "allow":
        return chosen[0]
    reasons = list(dict.fromkeys(d.reason for d in chosen))
    return Decision(chosen[0].action, "; ".join(reasons))


def _classify_segment(segment: str, preset: Preset) -> Decision:
    words = strip_wrappers(_words(segment))
    if not words:
        return ALLOW
    prog = os.path.basename(words[0])
    args = words[1:]
    shown = " ".join(words)

    # Nested shells: classify the -c string like a top-level command.
    if prog in _SHELLS and "-c" in args:
        idx = args.index("-c")
        if idx + 1 < len(args):
            return classify_command(args[idx + 1], preset)
    if prog == "eval":
        return classify_command(" ".join(args), preset)

    if prog == "git":
        git = _classify_git(args, shown)
        if git is not None:
            return git
    if _is_package_install(prog, args):
        if preset == "permissive":
            return Decision("allow", f"package install: {shown}")
        return Decision("ask", f"package install: {shown}")
    if preset == "cautious":
        return Decision("ask", f"shell command: {shown}")
    return ALLOW


# --- git ---------------------------------------------------------------------------

_GIT_OPTS_WITH_ARG = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
)


def _classify_git(args: list[str], shown: str) -> Decision | None:
    aliases: dict[str, str] = {}
    i = 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i]
        if opt in _GIT_OPTS_WITH_ARG and i + 1 < len(args):
            if opt == "-c":
                _record_alias(args[i + 1], aliases)
            i += 2
        else:
            if opt.startswith("-c") and len(opt) > 2:
                _record_alias(opt[2:], aliases)
            i += 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1 :]
    expansion = aliases.get(sub)
    if expansion is not None:
        if re.search(r"\bpush\b", expansion):
            return Decision("deny", GIT_PUSH_REASON)
        exp_words = _words(expansion.lstrip("!"))
        if exp_words:
            sub, rest = exp_words[0], exp_words[1:] + rest

    positional = [a for a in rest if not a.startswith("-")]
    if sub == "push":
        return Decision("deny", GIT_PUSH_REASON)
    if sub in ("subtree", "lfs") and positional[:1] == ["push"]:
        return Decision("deny", GIT_PUSH_REASON)

    destructive = Decision("ask", f"destructive git: {shown}")
    if sub == "reset" and "--hard" in rest:
        return destructive
    if sub == "clean" and any(_is_force_flag(a) for a in rest):
        return destructive
    if sub == "checkout":
        after_dashes = rest[rest.index("--") + 1 :] if "--" in rest else []
        if "." in rest or after_dashes:
            return destructive
    if sub == "restore" and any(a in (".", ":/", "*", "./") for a in positional):
        return destructive
    return None


def _record_alias(setting: str, aliases: dict[str, str]) -> None:
    key, _, value = setting.partition("=")
    if key.lower().startswith("alias."):
        aliases[key[len("alias.") :]] = value


def _is_force_flag(arg: str) -> bool:
    if arg == "--force":
        return True
    return arg.startswith("-") and not arg.startswith("--") and "f" in arg[1:]


# --- package installs ------------------------------------------------------------------

_NPM_INSTALL = frozenset(
    {"install", "i", "in", "ins", "inst", "insta", "instal", "isnt", "isnta", "isntal",
     "isntall", "add", "ci"}
)  # fmt: skip
_JS_OPTS_WITH_ARG = frozenset({"-C", "--dir", "--prefix", "--filter", "-F", "--cwd", "-w"})


def _positional(args: list[str], opts_with_arg: frozenset[str] = frozenset()) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            i += 2 if a in opts_with_arg else 1
            continue
        out.append(a)
        i += 1
    return out


def _is_package_install(prog: str, args: list[str]) -> bool:
    pos = _positional(args, _JS_OPTS_WITH_ARG)
    first = pos[0] if pos else None
    if prog == "npm":
        return first in _NPM_INSTALL
    if prog == "pnpm":
        return first in ("add", "install", "i", "in")
    if prog == "bun":
        return first in ("add", "a", "install", "i")
    if prog == "yarn":
        if first is None or first in ("add", "install"):
            return True
        if first == "global":
            return pos[1:2] == ["add"]
        if first == "workspace":
            return len(pos) > 2 and pos[2] in ("add", "install")
        return False
    if re.fullmatch(r"pip(3(\.\d+)?)?", prog) or prog == "pipx":
        return first == "install"
    if re.fullmatch(r"python(3(\.\d+)?)?", prog):
        if "-m" in args:
            idx = args.index("-m")
            mod_args = args[idx + 1 :]
            return mod_args[:1] == ["pip"] and _positional(mod_args[1:])[:1] == ["install"]
        return False
    if prog == "uv":
        if first == "add":
            return True
        return first in ("pip", "tool") and pos[1:2] == ["install"]
    if prog == "brew":
        return first in ("install", "reinstall")
    return False
